import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import read_briefing, connect, process_updates, read_news, read_links, find_link_group, rebuild_articles, simulation_news


class NewsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name) / "test.sqlite3")
        self.message = {
            "chat": {"id": -100123, "type": "channel", "title": "Hermes"},
            "message_id": 42,
            "date": int(datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc).timestamp()),
            "text": "기술 뉴스\n오늘의 메시지입니다.",
        }

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def ingest(self, message=None, update_id=1, edited=False):
        process_updates(self.db, [{"update_id": update_id,
                                  "edited_channel_post" if edited else "channel_post": message or self.message}],
                        {"-100123"})

    def test_korean_midnight_and_link(self):
        self.ingest()
        result = read_news(self.db, {})
        self.assertEqual(result["date"], "2026-09-12")
        self.assertEqual(result["items"][0]["url"], "https://t.me/c/123/42")
        self.assertEqual(read_news(self.db, {"date": ["2026-09-11"]})["items"], [])

    def test_replay_and_edits_are_idempotent(self):
        self.ingest()
        self.ingest()
        self.ingest(dict(self.message, text="수정된 소식", edit_date=self.message["date"]+10), 2, True)
        self.ingest(update_id=3)
        result = read_news(self.db, {})
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["text"], "수정된 소식")
        self.assertEqual(self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0], "4")

    def test_channel_allowlist(self):
        message = dict(self.message, chat={"id": -100456, "type": "channel"})
        self.ingest(message)
        self.assertEqual(read_news(self.db, {})["items"], [])

    def test_search_and_filters(self):
        self.ingest()
        self.assertEqual(len(read_news(self.db, {"q": ["기술"]})["items"]), 1)
        self.assertEqual(read_news(self.db, {"q": ["없는단어"]})["items"], [])
        self.assertEqual(read_news(self.db, {"channel": ["-100456"]})["items"], [])
        self.assertEqual(read_news(self.db, {"q": ["%' OR 1=1 --"]})["items"], [])

    def test_batch_rolls_back_cursor_and_messages(self):
        with self.assertRaises(KeyError):
            process_updates(self.db, [
                {"update_id": 1, "channel_post": self.message},
                {"update_id": 2, "channel_post": {"chat": self.message["chat"], "text": "broken"}},
            ], {"-100123"})
        self.assertEqual(read_news(self.db, {})["items"], [])
        self.assertIsNone(self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone())

    def test_caption_and_removed_caption(self):
        caption = dict(self.message, text="", caption="사진 설명")
        self.ingest(caption)
        self.assertEqual(read_news(self.db, {})["items"][0]["text"], "사진 설명")
        self.ingest(dict(caption, caption="", edit_date=self.message["date"]+1), 2, True)
        self.assertEqual(read_news(self.db, {})["items"], [])

    def test_invalid_date(self):
        with self.assertRaises(ValueError):
            read_news(self.db, {"date": ["2026-02-30"]})

    def test_split_topic_filters_and_date_counts(self):
        self.ingest(dict(self.message, text=(
            "📰 AI 뉴스 (2026-07-05 06:02)\n\n"
            "- [2026-07-04] 로봇 자율주행 기술 발표 — 로봇 제어 기술 소식\n"
            "  링크: https://example.com/robotics\n\n"
            "- [2026-07-03] 의료 AI 암 진단 모델 — 병원 진단 연구\n"
            "  링크: https://example.com/medical\n"
        )))
        all_news = read_news(self.db, {"date": ["all"]})
        self.assertEqual(len(all_news["items"]), 2)
        self.assertEqual({item["day"] for item in all_news["items"]}, {"2026-07-03", "2026-07-04"})
        self.assertEqual(sum(date["count"] for date in all_news["dates"]), 2)
        medical = read_news(self.db, {"date": ["all"], "topic": ["medical"]})
        self.assertEqual(len(medical["items"]), 1)
        self.assertEqual(medical["items"][0]["day"], "2026-07-03")
        self.assertEqual(sum(topic["count"] for topic in medical["topics"]), 2)

    def test_duplicate_article_retains_both_telegram_sources(self):
        body = "- [2026-07-04] 로봇 제어 기술 발표 — 자율주행 개선\n링크: https://example.com/robotics"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, message_id=43, text=body+"?utm_source=telegram"), 2)
        items = read_news(self.db, {"date": ["all"]})["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_count"], 2)
        self.assertEqual({source["message_id"] for source in items[0]["sources"]}, {42, 43})

    def test_identical_messages_across_days_are_excluded_everywhere(self):
        body = "로봇 소식\nhttps://example.com/robotics"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, message_id=43, date=self.message["date"]+86400,
                         text=body.replace("\n", "\n\n  ")), 2)
        items = read_news(self.db, {"date": ["all"]})["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message_id"], 42)
        self.assertEqual(read_news(self.db, {"date": ["2026-09-13"]})["items"], [])
        group = read_links(self.db, {})["groups"][0]
        self.assertEqual(group["occurrence_count"], 1)
        self.assertEqual(group["distinct_days"], 1)
        self.assertEqual(read_links(self.db, {"repeated": ["1"]})["total"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM news").fetchone()[0], 2)

    def test_same_link_with_changed_description_is_retained(self):
        body = "- [2026-07-04] 로봇 제어 기술 — 성능 10% 개선\nhttps://example.com/robotics"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, message_id=43, text=body.replace("10%", "20%")), 2)
        self.assertEqual(len(read_news(self.db, {"date": ["all"]})["items"]), 2)
        self.assertEqual(read_links(self.db, {})["groups"][0]["occurrence_count"], 2)

    def test_duplicate_edit_and_original_removal_reselect_representative(self):
        body = "로봇 소식\nhttps://example.com/robotics"
        self.ingest(dict(self.message, text=body))
        second = dict(self.message, message_id=43, date=self.message["date"]+86400, text=body)
        self.ingest(second, 2)
        self.assertEqual(read_links(self.db, {})["groups"][0]["occurrence_count"], 1)
        self.ingest(dict(second, text=body+"\n새 설명", edit_date=second["date"]+1), 3, True)
        self.assertEqual(read_links(self.db, {})["groups"][0]["occurrence_count"], 2)
        self.ingest(dict(second, edit_date=second["date"]+2), 4, True)
        self.ingest(dict(self.message, text="", edit_date=second["date"]+3), 5, True)
        detail = find_link_group(self.db, read_links(self.db, {})["groups"][0]["id"])
        self.assertEqual([m["message_id"] for m in detail["mentions"]], [43])

    def test_duplicate_orphan_links_do_not_reappear(self):
        body = "링크: https://example.com/previous-story\n\n- [2026-07-05] 논문 소개\nhttps://arxiv.org/abs/2607.12345"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, message_id=43, text=body), 2)
        groups = read_links(self.db, {})["groups"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g["occurrence_count"] == 1 for g in groups))

    def test_hidden_links_with_same_label_are_not_duplicate_messages(self):
        body = "새 논문 읽기"
        def message(mid, url):
            return dict(self.message, message_id=mid, text=body,
                        entities=[{"type": "text_link", "offset": 0, "length": len(body), "url": url}])
        self.ingest(message(42, "https://arxiv.org/abs/2607.12345"))
        self.ingest(message(43, "https://arxiv.org/abs/2607.54321"), 2)
        groups = read_links(self.db, {})["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(read_news(self.db, {"date": ["all"]})["items"]), 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM archived_urls WHERE active=1").fetchone()[0], 2)

    def test_duplicate_hidden_link_keeps_all_archive_copies(self):
        body = "논문 읽기"
        message = dict(self.message, text=body, entities=[
            {"type": "text_link", "offset": 0, "length": len(body), "url": "https://arxiv.org/abs/2607.12345"}])
        self.ingest(message)
        self.ingest(dict(message, message_id=43), 2)
        self.assertEqual(read_links(self.db, {})["groups"][0]["occurrence_count"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM archived_urls").fetchone()[0], 2)

    def test_archive_is_committed_and_edit_retains_history(self):
        body = "자료 https://example.com/old"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, text="자료 https://example.com/new", edit_date=self.message["date"]+1), 2, True)
        db = connect(Path(self.temp.name) / "test.sqlite3")
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM archived_urls").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT original_url FROM archived_urls WHERE active=1").fetchone()[0], "https://example.com/new")
        finally:
            db.close()

    def test_simulation_snapshot_uses_exact_selected_news_scope(self):
        self.ingest(dict(self.message, text="- [2026-07-04] 로봇 제어 실험\nhttps://example.com/robot"))
        self.ingest(dict(self.message, message_id=43, text="- [2026-07-05] 반도체 설계 실험\nhttps://example.com/chip"), 2)
        selected = simulation_news(self.db, {"filters": {"date": "2026-07-04", "q": "로봇"}})
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_url"], "https://example.com/robot")
        prepared = simulation_news(self.db, {"filters": {"date": "2026-07-04", "q": "로봇"}}, prepared=True)
        self.assertEqual([i['source_url'] for i in prepared], [i['source_url'] for i in selected])
        self.assertEqual(len(simulation_news(self.db, {"filters": {}})), 2)
        with self.assertRaises(ValueError):
            simulation_news(self.db, {"filters": {"unknown": "value"}})
        with self.assertRaises(ValueError):
            simulation_news(self.db, {"filters": {"q": ["bad"]}})

    def test_editorial_briefing_keeps_archive_and_related_history(self):
        body = "- [2026-07-04] 로봇 제어 기술 — 성능 개선 내용\nhttps://example.com/robotics"
        self.ingest(dict(self.message, text=body))
        self.ingest(dict(self.message, message_id=43, text=body.replace("07-04", "07-05").replace("성능 개선", "지연시간 20% 개선")), 2)
        result = read_briefing(self.db, {"date": ["2026-07-05"]})
        self.assertEqual(result["lead"]["change_type"], "updated")
        self.assertEqual(result["lead"]["related"], [])
        self.assertEqual(result["lead"]["timeline"]["total"], 2)
        self.assertEqual(result["stats"]["selected_count"], 1)
        self.assertEqual(len(read_news(self.db, {"date": ["all"]})["items"]), 2)
        self.assertTrue(result["dates"])
        all_result = read_briefing(self.db, {"date": ["all"]})
        self.assertNotIn("all", all_result["lead"]["timeline"]["dates"])

    def test_reclassification_preserves_original_and_cursor(self):
        self.ingest()
        before = tuple(self.db.execute("SELECT * FROM news").fetchone())
        rebuild_articles(self.db)
        rebuild_articles(self.db)
        self.assertEqual(tuple(self.db.execute("SELECT * FROM news").fetchone()), before)
        self.assertEqual(self.db.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0], "2")
        self.assertEqual(len(read_news(self.db, {})["items"]), 1)

    def test_edit_replaces_derived_date_and_topic(self):
        self.ingest(dict(self.message, text="- [2026-07-04] 로봇 제어 기술\nhttps://example.com/robotics"))
        self.ingest(dict(self.message, text="- [2026-07-05] 의료 AI 암 진단\nhttps://example.com/medical",
                         edit_date=self.message["date"]+10), 2, True)
        self.assertEqual(read_news(self.db, {"date": ["2026-07-04"]})["items"], [])
        items = read_news(self.db, {"date": ["2026-07-05"], "topic": ["medical"]})["items"]
        self.assertEqual(len(items), 1)
        self.ingest(dict(self.message, text="", edit_date=self.message["date"]+20), 3, True)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 0)

    def test_link_view_keeps_cross_day_history_and_types(self):
        self.ingest(dict(self.message, text="- [2026-07-04] 논문 소개\nhttps://arxiv.org/abs/2607.12345v1"))
        self.ingest(dict(self.message, message_id=43,
                         text="- [2026-07-05] 논문 추가 설명\nhttps://arxiv.org/pdf/2607.12345v2.pdf"), 2)
        result = read_links(self.db, {"date": ["2026-07-04"], "content_type": ["paper"], "repeated": ["1"]})
        self.assertEqual(result["total"], 1)
        item = result["groups"][0]
        self.assertEqual(item["distinct_days"], 2)
        self.assertEqual(item["occurrence_count"], 2)
        self.assertEqual(len(find_link_group(self.db, item["id"])["mentions"]), 2)
        self.assertEqual(read_links(self.db, {"content_type": ["blog"]})["total"], 0)
        daily = read_news(self.db, {"date": ["all"], "content_type": ["paper"]})
        self.assertEqual(len(daily["items"]), 2)

    def test_link_cache_rebuilds_after_edit(self):
        self.ingest(dict(self.message, text="- [2026-07-04] 첫 논문\nhttps://arxiv.org/abs/2607.12345"))
        old_id = read_links(self.db, {})["groups"][0]["id"]
        self.ingest(dict(self.message, text="- [2026-07-04] 두 번째 논문\nhttps://arxiv.org/abs/2607.54321",
                         edit_date=self.message["date"]+10), 2, True)
        self.assertIsNone(find_link_group(self.db, old_id))
        self.assertEqual(read_links(self.db, {})["total"], 1)

    def test_source_only_fragment_is_not_lost_when_next_article_exists(self):
        self.ingest(dict(self.message, text=(
            "링크: https://example.com/previous-story\n\n"
            "- [2026-07-05] 다음 논문 소개\nhttps://arxiv.org/abs/2607.12345")))
        groups = read_links(self.db, {})["groups"]
        self.assertEqual(len(groups), 2)
        orphan = next(group for group in groups if "previous-story" in group["canonical_url"])
        detail = find_link_group(self.db, orphan["id"])
        self.assertEqual(detail["mentions"][0]["date_basis"], "telegram")


if __name__ == "__main__":
    unittest.main()
