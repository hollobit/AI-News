import copy
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_graph import (
    GRAPH_SCHEMA,
    NODE_TYPES,
    analyze_graph,
    build_graph_input,
    validate_graph,
)


def row(message_id, day, *, title="GPT 연구", text="OpenAI가 GPT를 발표했다.", source_url="https://example.com/a",
        topic="models", channel="news", item_index=0):
    return {
        "chat_id": "-1001", "message_id": message_id, "item_index": item_index,
        "title": title, "text": text, "excerpt": text, "source_url": source_url,
        "day": day, "telegram_day": day, "date_basis": "article", "topic": topic,
        "channel": channel, "url": f"https://t.me/c/1/{message_id}",
        "published_at": f"{day}T09:00:00+09:00",
    }


def graph_result(evidence_id):
    return {
        "summary": "OpenAI와 GPT의 관계를 메시지 설명에서 추출했다.",
        "nodes": [
            {"id": "openai", "name": "OpenAI", "type": "Organization", "summary": "AI 조직",
             "aliases": [], "evidence_ids": [evidence_id]},
            {"id": "gpt", "name": "GPT", "type": "Technology", "summary": "언어 모델 기술",
             "aliases": ["GPT 모델"], "evidence_ids": [evidence_id]},
        ],
        "edges": [
            {"source": "openai", "target": "gpt", "relation": "발표",
             "meaning": "메시지는 OpenAI가 GPT를 발표했다고 설명한다.",
             "evidence_ids": [evidence_id], "confidence": "attributed"},
        ],
        "cautions": ["메시지 발췌만 분석했다."],
    }


class KnowledgeGraphTests(unittest.TestCase):
    def test_vendored_ontology_is_unmodified_and_attributed(self):
        local = Path("vendor/mirofish/ontology.py").read_bytes()
        self.assertEqual(hashlib.sha256(local).hexdigest(),
                         "c220fff465ae465ad6c8b2fc263ca623a37394e78a4b9d0d8ad7bfc114e88d5c")
        self.assertEqual(hashlib.sha256(Path("vendor/mirofish/LICENSE").read_bytes()).hexdigest(),
                         "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef")
        origin = Path("vendor/mirofish/ORIGIN.md").read_text(encoding="utf-8")
        self.assertIn("39d849138ef254f6c737ab4c4705e5545dbe31d4", origin)
        self.assertIn("GNU Affero General Public License", origin)

    def test_build_input_filters_scope_keeps_all_urls_and_removes_duplicates(self):
        first = row(1, "2026-09-01", text="설명 https://a.test/one 및 https://b.test/two", source_url="https://a.test/one")
        duplicate = row(2, "2026-09-02", text=first["text"], source_url=first["source_url"])
        other = row(3, "2026-09-03", title="로봇", text="로봇 소식", source_url="https://c.test/three", topic="robotics")
        group = build_graph_input([first, duplicate, other], {"date": ["all"], "topic": ["models"]})
        self.assertRegex(group["id"], r"^[0-9a-f]{24}$")
        self.assertEqual(group["total_available"], 1)
        self.assertEqual(group["duplicate_count"], 1)
        self.assertEqual(group["mentions"][0]["source_urls"], ["https://a.test/one", "https://b.test/two"])
        self.assertEqual(group["scope"]["topic"], "models")
        self.assertEqual(set(group["ontology"]["node_types"]), set(NODE_TYPES))

        by_id = build_graph_input([first, other], {"channel": ["-1001"]})
        by_title = build_graph_input([first, other], {"channel": ["NEWS"]})
        self.assertEqual(by_id["total_available"], 2)
        self.assertEqual(by_title["total_available"], 2)

    def test_sampling_is_bounded_across_timeline_and_disclosed(self):
        rows = [row(index, f"2026-09-{index:02d}", text=(f"설명 {index} " + "가" * 2500),
                    source_url=f"https://example.com/{index}") for index in range(1, 31)]
        group = build_graph_input(rows, {})
        self.assertEqual(group["total_available"], 30)
        self.assertEqual(group["selected_count"], 24)
        self.assertEqual(group["evidence"][0]["day"], "2026-09-01")
        self.assertEqual(group["evidence"][-1]["day"], "2026-09-30")
        self.assertTrue(all(len(item["text"]) <= 1800 for item in group["evidence"]))
        self.assertEqual(group["selection"]["limit"], 24)

    def test_hidden_link_supplement_merges_into_source_less_message(self):
        article = row(7, "2026-09-07", title="숨은 링크", text="동일한 설명", source_url="", item_index=0)
        article["embedded_urls"] = ["https://hidden.test/paper"]
        archived = row(7, "2026-09-07", title="숨은 링크", text="동일한 설명",
                       source_url="https://hidden.test/paper", item_index=-7)
        archived["archive_snapshot_id"] = "snapshot-1"
        group = build_graph_input([article, archived], {})
        self.assertEqual(group["total_available"], 1)
        self.assertEqual(group["duplicate_count"], 1)
        self.assertEqual(group["mentions"][0]["source_urls"], ["https://hidden.test/paper"])
        self.assertEqual(group["mentions"][0]["topic"], "models")
        self.assertFalse(any(key.startswith("_") for key in group["mentions"][0]))

    def test_explicit_article_stays_separate_from_hidden_link_supplement(self):
        article = row(8, "2026-09-08", title="두 자료", text="같은 주변 설명",
                      source_url="https://article.test/story", item_index=0)
        article["embedded_urls"] = ["https://hidden.test/reference"]
        archived = row(8, "2026-09-08", title="두 자료", text="같은 주변 설명",
                       source_url="https://hidden.test/reference", item_index=-8)
        archived["archive_snapshot_id"] = "snapshot-2"
        group = build_graph_input([article, archived], {})
        self.assertEqual(group["total_available"], 2)
        self.assertEqual({tuple(item["source_urls"]) for item in group["mentions"]}, {
            ("https://article.test/story",), ("https://hidden.test/reference",),
        })

    def test_dataset_hash_changes_when_unsampled_evidence_changes(self):
        rows = [row(index, f"2026-09-{index:02d}", text=f"설명 {index}", source_url=f"https://x.test/{index}")
                for index in range(1, 31)]
        first = build_graph_input(rows, {})
        selected_ids = {item["id"] for item in first["evidence"]}
        changed = copy.deepcopy(rows)
        target = next(item for item in changed if build_graph_input([item], {})["mentions"][0]["id"] not in selected_ids)
        target["text"] += " 수정"
        second = build_graph_input(changed, {})
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["dataset_hash"], second["dataset_hash"])

    def test_validate_graph_accepts_grounded_graph(self):
        group = build_graph_input([row(1, "2026-09-01")], {})
        result = graph_result(group["evidence"][0]["id"])
        self.assertIs(validate_graph(result, group["evidence"]), result)

    def test_extended_country_company_institution_policy_ontology(self):
        group = build_graph_input([row(1, "2026-09-01")], {})
        evidence_id = group["evidence"][0]["id"]
        result = {
            "summary": "국가, 기업, 기관과 정책의 관계",
            "nodes": [
                {"id": "kr", "name": "한국", "type": "Country", "summary": "국가",
                 "aliases": ["대한민국"], "evidence_ids": [evidence_id]},
                {"id": "company", "name": "기업 A", "type": "Company", "summary": "기업",
                 "aliases": [], "evidence_ids": [evidence_id]},
                {"id": "institute", "name": "연구원 B", "type": "Institution", "summary": "연구 기관",
                 "aliases": [], "evidence_ids": [evidence_id]},
                {"id": "policy", "name": "AI 정책", "type": "Policy", "summary": "공식 정책",
                 "aliases": [], "evidence_ids": [evidence_id]},
            ],
            "edges": [
                {"source": "kr", "target": "policy", "relation": "시행", "meaning": "국가가 정책을 시행",
                 "evidence_ids": [evidence_id], "confidence": "attributed"},
                {"source": "company", "target": "institute", "relation": "협력", "meaning": "공동 연구",
                 "evidence_ids": [evidence_id], "confidence": "attributed"},
            ],
            "cautions": [],
        }
        self.assertIs(validate_graph(result, group["evidence"]), result)
        self.assertTrue({"Country", "Company", "Institution", "Policy"}.issubset(NODE_TYPES))

    def test_validate_graph_rejects_fabricated_evidence_and_unknown_endpoint(self):
        group = build_graph_input([row(1, "2026-09-01")], {})
        result = graph_result(group["evidence"][0]["id"])
        fabricated = copy.deepcopy(result)
        fabricated["nodes"][0]["evidence_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_graph(fabricated, group["evidence"])
        unknown = copy.deepcopy(result)
        unknown["edges"][0]["target"] = "missing"
        with self.assertRaises(ValueError):
            validate_graph(unknown, group["evidence"])
        malformed = copy.deepcopy(result)
        malformed["edges"][0]["source"] = []
        with self.assertRaises(ValueError):
            validate_graph(malformed, group["evidence"])

    def test_validate_graph_rejects_duplicate_exact_edge(self):
        group = build_graph_input([row(1, "2026-09-01")], {})
        result = graph_result(group["evidence"][0]["id"])
        result["edges"].append(copy.deepcopy(result["edges"][0]))
        with self.assertRaisesRegex(ValueError, "중복"):
            validate_graph(result, group["evidence"])

    def test_analyze_graph_uses_structured_schema_and_adds_disclosures(self):
        group = build_graph_input([row(1, "2026-09-01")], {})
        generated = graph_result(group["evidence"][0]["id"])
        with patch("semantic.run_structured", return_value=generated) as structured:
            result = analyze_graph(group)
        self.assertEqual(structured.call_args.args[1], GRAPH_SCHEMA)
        self.assertIn("Telegram", structured.call_args.args[0])
        self.assertIn("Country, Company, Institution, Policy", structured.call_args.args[0])
        self.assertEqual(result["basis"], "telegram_excerpts")
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["total_available"], 1)
        self.assertIn("analyzed_at", result)


if __name__ == "__main__":
    unittest.main()
