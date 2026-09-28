import unittest

from briefing import build_briefing
from link_groups import build_link_groups


def article(message_id, day, title, description, url, topic="models", content_type="other"):
    return {
        "chat_id": "-1001", "message_id": message_id, "item_index": 0,
        "title": title, "excerpt": description, "text": f"{title}\n{description}\n{url}",
        "day": day, "date_basis": "article", "topic": topic,
        "topic_title": topic, "source_url": url, "content_type": content_type,
        "content_type_title": content_type, "kind": "article", "channel": "Hermes",
        "url": f"https://t.me/c/1/{message_id}",
        "published_at": f"{day}T09:00:00+09:00", "telegram_day": day,
        "sources": [{"url": f"https://t.me/c/1/{message_id}"}],
    }


def payload(day, items):
    return {"date": day, "items": items, "dates": [], "topics": [], "types": []}


class BriefingTests(unittest.TestCase):
    def test_repeat_frequency_alone_does_not_promote_a_thin_story(self):
        day = "2026-09-12"
        repeated = [
            article(index, f"2026-09-{index:02d}", "반복 링크", "같은 소식", "https://repeat.test/a")
            for index in range(1, 13)
        ]
        substantive = article(
            20, day, "새 모델 평가 결과 발표",
            "독립 평가에서 정확도 18% 개선 결과를 공개했고 적용 대상과 측정 기준도 함께 설명했다.",
            "https://new.test/report", topic="research",
        )
        groups = build_link_groups(repeated + [substantive])
        result = build_briefing(payload(day, [repeated[-1], substantive]), groups)
        self.assertEqual(result["lead"]["title"], substantive["title"])
        self.assertEqual(result["stats"]["recurring_count"], 1)

    def test_editorial_selection_covers_distinct_topics_before_refilling(self):
        day = "2026-09-12"
        items = [
            article(1, day, "모델 A 공개", "모델 A 성능 20% 개선과 평가 기준을 자세히 공개했다.", "https://a.test/1", "models"),
            article(2, day, "모델 B 공개", "모델 B 성능 19% 개선과 평가 기준을 자세히 공개했다.", "https://a.test/2", "models"),
            article(3, day, "로봇 정책 발표", "로봇 안전 규제 적용 일정을 발표했다.", "https://a.test/3", "robotics"),
            article(4, day, "의료 승인 소식", "의료 AI 승인 결과를 공개했다.", "https://a.test/4", "medical"),
        ]
        result = build_briefing(payload(day, items), build_link_groups(items), {"highlights": 2})
        selected = [result["lead"], *result["highlights"]]
        self.assertEqual(len(selected), 3)
        self.assertEqual({story["topic"] for story in selected}, {"models", "robotics", "medical"})
        self.assertEqual(result["stats"]["available_count"], 4)
        self.assertEqual(sum(topic["count"] for topic in result["topics"]), 4)

    def test_same_url_is_updated_only_when_description_changes(self):
        url = "https://example.test/model"
        old = article(1, "2026-09-10", "모델 공개", "성능 10% 개선", url)
        repeated = article(2, "2026-09-11", "모델 공개", "성능 10% 개선", url)
        updated = article(3, "2026-09-12", "모델 후속", "성능 20% 개선", url)
        groups = build_link_groups([old, repeated, updated])
        recurring_result = build_briefing(payload("2026-09-11", [repeated]), groups)
        updated_result = build_briefing(payload("2026-09-12", [updated]), groups)
        self.assertEqual(recurring_result["lead"]["change_type"], "recurring")
        self.assertEqual(updated_result["lead"]["change_type"], "updated")
        self.assertIn("이전 기록과 달라짐", updated_result["lead"]["why_selected"])

    def test_related_contains_only_other_news_with_actual_shared_keywords(self):
        old = article(1, "2026-09-10", "OpenAI GPT benchmark results", "첫 평가 결과", "https://a.test/1")
        current = article(2, "2026-09-12", "OpenAI GPT benchmark results update", "새 평가 결과", "https://a.test/1")
        candidate = article(3, "2026-09-11", "GPT benchmark safety results", "안전 평가", "https://b.test/2")
        unrelated = article(4, "2026-09-11", "Robot policy schedule", "정책 일정", "https://c.test/3")
        groups = build_link_groups([old, current, candidate, unrelated])
        result = build_briefing(payload("2026-09-12", [current]), groups)
        related = result["lead"]["related"]
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0]["source_url"], "https://b.test/2")
        self.assertEqual(related[0]["relation_type"], "shared_keywords")
        self.assertGreaterEqual(set(related[0]["shared_keywords"]), {"benchmark", "gpt"})
        self.assertIn("인과관계 미확인", related[0]["relationship_label"])

    def test_related_excludes_same_link_history_and_duplicate_title_mirrors(self):
        old = article(1, "2026-09-10", "OpenAI GPT benchmark results", "첫 기록", "https://a.test/1")
        current = article(2, "2026-09-12", "OpenAI GPT benchmark results", "후속 기록", "https://a.test/1")
        mirror = article(3, "2026-09-11", "OpenAI GPT benchmark results", "다른 주소의 복제", "https://mirror.test/1")
        groups = build_link_groups([old, current, mirror])
        source = next(group for group in groups if group["canonical_url"] == "https://a.test/1")
        mirror_group = next(group for group in groups if group["canonical_url"] == "https://mirror.test/1")
        # Exercise both upstream candidates and a malformed same-group candidate.
        source["related"].append({
            "id": source["id"], "title": source["title"],
            "canonical_url": source["canonical_url"], "score": 1,
        })
        self.assertIn(mirror_group["id"], {candidate["id"] for candidate in source["related"]})
        result = build_briefing(payload("2026-09-12", [current]), groups)
        self.assertEqual(result["lead"]["related"], [])
        self.assertEqual(result["lead"]["timeline"]["total"], 2)

    def test_complete_saved_analysis_can_supply_summary(self):
        day = "2026-09-12"
        item = article(1, day, "논문 소개", "짧은 설명", "https://arxiv.org/abs/2609.1", "research")
        groups = build_link_groups([item])
        groups[0]["analysis"] = {"status": "complete", "result": {"summary": "저장된 근거 기반 상세 요약"}}
        result = build_briefing(payload(day, [item]), groups)
        self.assertEqual(result["lead"]["summary"], "저장된 근거 기반 상세 요약")
        self.assertEqual(result["lead"]["content_type"], "paper")

    def test_long_timeline_reports_hidden_date_count(self):
        rows = [article(index, f"2026-09-{index:02d}", "연속 업데이트", f"설명 {index}", "https://series.test/a")
                for index in range(1, 9)]
        result = build_briefing(payload("2026-09-08", [rows[-1]]), build_link_groups(rows))
        timeline = result["lead"]["timeline"]
        self.assertEqual(timeline["total"], 8)
        self.assertEqual(len(timeline["dates"]), 6)
        self.assertEqual(timeline["hidden_count"], 2)
        self.assertEqual(timeline["dates"][0], "2026-09-01")
        self.assertEqual(timeline["dates"][-1], "2026-09-08")


if __name__ == "__main__":
    unittest.main()
