import json
import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from graph_rag import GraphResult, answer_question, build_retrieval, load_integrated_graph


def saved_graph(evidence_id="m1", url="https://example.com/story", day="2026-09-10"):
    return {
        "summary": "요약",
        "nodes": [
            {"id": "openai", "name": "OpenAI", "type": "Company", "summary": "AI 기업",
             "aliases": ["오픈AI"], "evidence_ids": [evidence_id]},
            {"id": "model", "name": "GPT", "type": "Concept", "summary": "언어 모델",
             "aliases": [], "evidence_ids": [evidence_id]},
        ],
        "edges": [{"source": "openai", "target": "model", "relation": "개발",
                   "meaning": "모델을 개발한다고 소개됨", "evidence_ids": [evidence_id],
                   "confidence": "attributed"}],
        "cautions": [],
        "evidence": [{"id": evidence_id, "day": day, "topic": "models", "title": "모델 발표",
                      "text": "OpenAI가 GPT를 개발했다는 소개", "source_url": url}],
    }


class IntegratedGraphTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE news(chat_id TEXT, message_id INTEGER)")
        self.db.executemany("INSERT INTO news VALUES (?,?)", [("c", 1), ("c", 2), ("c", 3)])
        self.db.execute("CREATE TABLE graph_analysis(group_id TEXT PRIMARY KEY,input_hash TEXT,result TEXT,error TEXT,updated_at TEXT)")
        self.db.execute("""CREATE TABLE research_documents(
            run_id TEXT,doc_id TEXT,position INTEGER,kind TEXT,canonical_url TEXT,
            variants_json TEXT,titles_json TEXT,metadata_json TEXT,excerpt TEXT,
            source_status TEXT,source_title TEXT,source_text TEXT,status TEXT,
            analysis_json TEXT,error TEXT,PRIMARY KEY(run_id,doc_id))""")

    def tearDown(self):
        self.db.close()

    def add_graph(self, group_id, result):
        self.db.execute("INSERT INTO graph_analysis VALUES (?,?,?,?,?)",
                        (group_id, "hash", json.dumps(result, ensure_ascii=False), None, "now"))

    def add_research(self, doc_id="doc-1", url="https://example.com/story"):
        analysis = {
            "doc_id": doc_id, "summary": "기업과 모델 관계",
            "key_points": [{"text": "개발 관계", "evidence_doc_ids": [doc_id]}],
            "topics": ["models"], "countries": ["미국"],
            "players": [
                {"name": "OpenAI", "type": "company", "role": "개발 주체"},
                {"name": "GPT", "type": "other", "role": "모델"},
            ],
            "relations": [{"source": "OpenAI", "target": "GPT", "type": "개발",
                           "meaning": "모델 개발 관계", "evidence_doc_ids": [doc_id]}],
            "implications": [],
        }
        self.db.execute("INSERT INTO research_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "run", doc_id, 0, "url", url, "[]", json.dumps(["보관 제목"]),
            json.dumps([{"day": "2026-09-11"}, {"day": "2026-09-12"}]),
            "짧은 발췌", "fetched", "원문 제목", "OpenAI와 GPT 원문", "complete",
            json.dumps(analysis, ensure_ascii=False), None))

    def test_merges_completed_graph_and_research_with_global_provenance(self):
        self.add_graph("g1", saved_graph())
        self.add_research()
        graph = load_integrated_graph(self.db, {})
        self.assertEqual(3, graph["coverage"]["total_archive_messages"])
        self.assertEqual(2, graph["coverage"]["analyzed_source_records"])
        self.assertTrue(all(item["id"].startswith(("telegram:", "research:")) for item in graph["evidence"]))
        openai = next(item for item in graph["nodes"] if item["name"] == "OpenAI")
        # Company nodes merge; document support counts one canonical source.
        self.assertEqual("Company", openai["type"])
        self.assertEqual(1, openai["support_count"])
        edge = next(item for item in graph["edges"] if item["relation"] == "개발")
        self.assertEqual(1, edge["support_count"])
        self.assertEqual(2, len(edge["evidence_ids"]))
        dated = load_integrated_graph(self.db, {"date": ["2026-09-12"]})
        self.assertTrue(any(item["source_kind"] == "research" for item in dated["evidence"]))

    def test_reposts_do_not_increase_unique_document_support(self):
        self.add_graph("g1", saved_graph("m1"))
        self.add_graph("g2", saved_graph("m2", day="2026-09-11"))
        graph = load_integrated_graph(self.db, {})
        edge = graph["edges"][0]
        self.assertEqual(1, edge["support_count"])
        self.assertEqual(2, len(edge["evidence_ids"]))

    def test_date_topic_entity_and_relation_filters(self):
        self.add_graph("g1", saved_graph())
        empty = load_integrated_graph(self.db, {"date": ["2026-09-12"]})
        self.assertEqual([], empty["nodes"])
        graph = load_integrated_graph(self.db, {"date": ["2026-09-10"], "topic": ["models"],
                                                "relation_type": ["개발"]})
        self.assertEqual(2, len(graph["nodes"]))
        self.assertEqual(1, len(graph["edges"]))
        organizations = load_integrated_graph(self.db, {"entity_type": ["Company"]})
        self.assertEqual(["OpenAI"], [item["name"] for item in organizations["nodes"]])
        self.assertEqual([], organizations["edges"])

    def test_alias_collision_does_not_blindly_merge(self):
        result = saved_graph()
        result["nodes"] = [
            {"id": "a", "name": "Alpha", "type": "Organization", "summary": "a",
             "aliases": ["공통"], "evidence_ids": ["m1"]},
            {"id": "b", "name": "Beta", "type": "Organization", "summary": "b",
             "aliases": ["공통"], "evidence_ids": ["m1"]},
            {"id": "c", "name": "공통", "type": "Organization", "summary": "c",
             "aliases": ["Alpha", "Beta"], "evidence_ids": ["m1"]},
        ]
        result["edges"] = []
        self.add_graph("g1", result)
        graph = load_integrated_graph(self.db, {})
        self.assertEqual(3, len(graph["nodes"]))

    def test_query_and_focus_expand_neighbors_before_response(self):
        self.add_graph("g1", saved_graph())
        graph = load_integrated_graph(self.db, {"q": ["OpenAI"], "hops": ["1"]})
        self.assertEqual({"OpenAI", "GPT"}, {item["name"] for item in graph["nodes"]})
        none = load_integrated_graph(self.db, {"q": ["없는키워드"]})
        self.assertEqual([], none["nodes"])

    def add_workflow(self, run_id="w1", status="complete", payload=None, audited=True):
        self.db.execute("CREATE TABLE IF NOT EXISTS strategic_workflow_runs(id TEXT PRIMARY KEY,status TEXT,error TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT)")
        payload = payload if payload is not None else self.workflow_payload()
        if audited:
            payload["verification"].update({key:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                                           for key,value in (("report_hash",payload["report"]),("evidence_hash",payload["evidence"]))})
        self.db.execute("INSERT INTO strategic_workflow_runs VALUES (?,?,?)", (run_id,status,""))
        self.db.execute("INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)", (run_id,"final",json.dumps(payload)))

    def workflow_payload(self):
        return {"verified": True, "verification": {"accepted": True, "issues": [], "checked_evidence_ids": ["url_1"]},
                "report": {"summary": "전략 전망", "claims": [{"title": "소버린 AI 조달 기회", "detail": "공공 조달 수요를 관찰한다.",
                            "category": "opportunity", "uncertainty": "한정된 원문 발췌", "evidence_ids": ["url_1"]}], "limitations": []},
                "evidence": [{"id":"url_1", "origin":"fetched_url_excerpt", "url":"https://example.com/story",
                              "title":"정부 발표", "text":"정부가 AI 조달 사업을 발표했다.", "published_at":"2026-09-10"}]}

    def test_verified_workflow_citation_links_are_retrievable_with_provenance(self):
        self.add_graph("g1", saved_graph())
        self.add_workflow()
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(graph["coverage"]["completed_verified_workflows"],1)
        self.assertEqual(graph["coverage"]["analyzed_unique_documents"],1)
        edge=next(e for e in graph["edges"] if e["relation"]=="근거 인용")
        self.assertEqual(edge["confidence"],"reviewed_interpretation")
        self.assertIn("불확실성",edge["meaning"])
        evidence=next(e for e in graph["evidence"] if e["source_kind"]=="workflow")
        self.assertTrue(evidence["id"].startswith("workflow:"))
        self.assertEqual(evidence["source_record_id"],"url_1")
        self.assertEqual(evidence["evidence_origin"],"fetched_url_excerpt")
        self.assertEqual(evidence["workflow_run_ids"],["w1"])
        retrieved=build_retrieval(graph,"소버린 AI 조달")
        self.assertTrue(any(e["id"]==evidence["id"] for e in retrieved["evidence"]))

    def test_workflow_requires_complete_and_positive_full_verification(self):
        for index,status in enumerate(["needs_review","failed","running","paused"]):
            self.add_workflow(f"status-{index}",status)
        for index,mutate in enumerate([
            lambda p: p.update(verified=False),
            lambda p: p["verification"].update(accepted=False),
            lambda p: p["verification"].update(issues=["미해결"]),
            lambda p: p["verification"].update(checked_evidence_ids=[]),
            lambda p: p["report"]["claims"][0].update(status="needs_review"),
            lambda p: p["report"]["claims"][0].update(evidence_ids=["fabricated"]),
        ]):
            payload=self.workflow_payload()
            mutate(payload)
            self.add_workflow(f"audit-{index}",payload=payload)
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(graph["nodes"],[])
        self.assertEqual(graph["coverage"]["completed_verified_workflows"],0)

    def test_simulated_utterances_and_graph_retrieval_are_not_source_evidence(self):
        for index,origin in enumerate(["mirofish_simulation","agent_interview","graph_retrieval"]):
            payload=self.workflow_payload()
            payload["evidence"][0]["origin"]=origin
            self.add_workflow(f"sim-{index}",payload=payload)
        self.assertEqual(load_integrated_graph(self.db,{})["evidence"],[])

    def test_changed_or_unfingerprinted_audit_cannot_enter_graph(self):
        self.add_workflow("missing-hashes",audited=False)
        payload=self.workflow_payload()
        payload["verification"].update(report_hash="stale",evidence_hash="stale")
        self.add_workflow("stale-hashes",payload=payload,audited=False)
        self.assertEqual(load_integrated_graph(self.db,{})["edges"],[])

    def test_workflow_global_ids_stable_and_updated_excerpts_not_overwritten(self):
        self.add_workflow("w1")
        first=load_integrated_graph(self.db,{})["evidence"][0]["id"]
        self.add_workflow("w2")
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(len(graph["evidence"]),1)
        self.assertEqual(graph["evidence"][0]["id"],first)
        self.assertEqual(graph["evidence"][0]["workflow_run_ids"],["w1","w2"])
        payload=self.workflow_payload()
        payload["evidence"][0]["text"]="정부 발표 내용이 수정됐다."
        self.add_workflow("w3",payload=payload)
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(len(graph["evidence"]),2)
        self.assertEqual(graph["edges"][0]["support_count"],1)
        self.assertEqual(graph["coverage"]["analyzed_unique_documents"],1)


class RetrievalTests(unittest.TestCase):
    def graph(self):
        nodes = [
            {"id": "a", "name": "OpenAI", "type": "Organization", "summary": "AI 기업",
             "aliases": [], "evidence_ids": ["e1"], "support_count": 1},
            {"id": "b", "name": "GPT", "type": "Concept", "summary": "AI 모델",
             "aliases": [], "evidence_ids": ["e1"], "support_count": 1},
        ]
        edges = [{"id": "ab", "source": "a", "target": "b", "relation": "개발",
                  "meaning": "모델 개발", "evidence_ids": ["e1"], "support_count": 1}]
        evidence = [{"id": "e1", "day": "2026-09-10", "title": "발표", "text": "개발 발표"}]
        return GraphResult({"nodes": nodes, "edges": edges, "evidence": evidence},
                           full_nodes=nodes, full_edges=edges, full_evidence=evidence)

    def test_retrieval_is_bounded_and_no_hit_is_local(self):
        selected = build_retrieval(self.graph(), "OpenAI 모델")
        self.assertFalse(selected["no_hits"])
        self.assertEqual({"a", "b"}, {item["id"] for item in selected["nodes"]})
        self.assertTrue(build_retrieval(self.graph(), "무관한 질문")["no_hits"])

    def test_answer_validates_grounding_and_attaches_citations(self):
        response = {"answer": "개발 관계입니다.",
                    "claims": [{"text": "개발함", "evidence_ids": ["e1"]}],
                    "limitations": ["저장 근거 한정"]}
        audit = {'checks': [{'claim_index': 0, 'supported': True, 'reason': '원문 확인'}],
                 'answer_supported': True, 'missing_information': []}
        with patch("semantic.run_structured", side_effect=[response, audit]):
            answer = answer_question(self.graph(), "OpenAI 모델 관계")
        self.assertEqual("e1", answer["evidence"][0]["id"])
        self.assertEqual(2, len(answer["selected_nodes"]))

    def test_answer_rejects_fabricated_evidence_without_external_call_on_no_hit(self):
        response = {"answer": "잘못된 답", "claims": [{"text": "주장", "evidence_ids": ["fake"]}],
                    "limitations": []}
        with patch("semantic.run_structured", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "연결되지 않은"):
                answer_question(self.graph(), "OpenAI")
        with patch("semantic.run_structured") as run:
            answer = answer_question(self.graph(), "전혀없는질문")
        run.assert_not_called()
        self.assertEqual([], answer["claims"])


if __name__ == "__main__":
    unittest.main()
