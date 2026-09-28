import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import connect, process_updates, read_briefing, read_news
from keyword_index import init_keyword_index, read_keyword_record


class KeywordIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "keywords.sqlite3"
        self.db = connect(self.path)
        self.base_time = int(datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc).timestamp())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def ingest(self, message_id, day, title, description, url, update_id=None, edit_date=None):
        message = {
            "chat": {"id": -100123, "type": "channel", "title": "Hermes"},
            "message_id": message_id,
            "date": self.base_time,
            "text": f"- [{day}] {title} — {description}\n{url}",
        }
        key = "channel_post"
        if edit_date is not None:
            message["edit_date"] = edit_date
            key = "edited_channel_post"
        process_updates(self.db, [{"update_id": update_id or message_id, key: message}], {"-100123"})

    @staticmethod
    def term(payload, label):
        return next(keyword for keyword in payload["keywords"]
                    if keyword["label"].casefold() == label.casefold())

    def test_new_keyword_recurs_with_stable_id_and_persists(self):
        self.ingest(1, "2026-09-10", "OpenAI Orion 모델", "추론 성능 개선", "https://one.test/a")
        first = read_news(self.db, {"date": ["2026-09-10"]})
        openai = self.term(first["items"][0], "OpenAI")
        self.assertTrue(openai["is_new"])
        self.assertEqual(openai["first_seen"], "2026-09-10")
        self.assertIn("T", openai["first_observed_at"])

        self.ingest(2, "2026-09-11", "OpenAI 안전 평가", "안전 기준 확대", "https://two.test/b")
        second = read_news(self.db, {"date": ["2026-09-11"]})
        recurring = self.term(second["items"][0], "OpenAI")
        self.assertEqual(recurring["id"], openai["id"])
        self.assertFalse(recurring["is_new"])
        self.assertEqual(recurring["document_count"], 2)

        self.db.close()
        self.db = connect(self.path)
        persisted = self.term(read_news(self.db, {"date": ["2026-09-11"]})["items"][0], "OpenAI")
        self.assertEqual(persisted["id"], openai["id"])
        self.assertEqual(persisted["first_observed_at"], openai["first_observed_at"])

    def test_repeated_url_counts_as_one_document(self):
        url = "https://same.test/model"
        self.ingest(1, "2026-09-10", "Orion 모델", "성능 10% 개선", url)
        self.ingest(2, "2026-09-12", "Orion 모델", "성능 20% 개선", url)
        result = read_news(self.db, {"date": ["2026-09-12"]})
        self.assertEqual(self.term(result["items"][0], "Orion")["document_count"], 1)

    def test_edit_replaces_active_keywords_and_filter_results(self):
        self.ingest(1, "2026-09-12", "Orion 모델", "성능 개선", "https://edit.test/model")
        before = read_news(self.db, {"date": ["2026-09-12"]})
        old_id = self.term(before["items"][0], "Orion")["id"]
        self.ingest(1, "2026-09-12", "Helios 모델", "안전 개선", "https://edit.test/model",
                    update_id=2, edit_date=self.base_time + 10)
        after = read_news(self.db, {"date": ["2026-09-12"]})
        labels = {keyword["label"] for keyword in after["items"][0]["keywords"]}
        self.assertIn("Helios", labels)
        self.assertNotIn("Orion", labels)
        self.assertEqual(read_news(self.db, {"date": ["all"], "keyword": [old_id]})["items"], [])

    def test_keyword_filter_and_selected_relationships_are_exact(self):
        self.ingest(1, "2026-09-12", "OpenAI Orion benchmark", "모델 평가", "https://one.test/a")
        self.ingest(2, "2026-09-12", "OpenAI Helios policy", "정책 평가", "https://two.test/b")
        all_news = read_news(self.db, {"date": ["2026-09-12"]})
        openai_id = self.term(all_news["items"][0], "OpenAI")["id"]
        filtered = read_news(self.db, {"date": ["2026-09-12"], "keyword": [openai_id]})
        self.assertEqual(len(filtered["items"]), 2)
        discovery = filtered["keyword_discovery"]
        self.assertEqual(discovery["selected_keyword"]["id"], openai_id)
        self.assertTrue(discovery["relationships"])
        self.assertTrue(all(openai_id in {edge["source"], edge["target"]}
                            for edge in discovery["relationships"]))
        self.assertTrue(all(edge["relation_type"] == "co_occurs"
                            for edge in discovery["relationships"]))
        with self.assertRaises(ValueError):
            read_news(self.db, {"keyword": ["not-an-id"]})

    def test_unicode_is_normalized_and_briefing_keeps_keywords(self):
        self.ingest(1, "2026-09-12", "ＯｐｅｎＡＩ 로봇", "안전성 평가", "https://unicode.test/a")
        news = read_news(self.db, {"date": ["2026-09-12"]})
        self.assertEqual(self.term(news["items"][0], "OpenAI")["label"], "OpenAI")
        briefing = read_briefing(self.db, {"date": ["2026-09-12"]})
        self.assertEqual(briefing["lead"]["keywords"], news["items"][0]["keywords"])
        self.assertIn("first_observed_at", briefing["keyword_discovery"]["top_keywords"][0])

    def test_lazy_record_contains_generic_numeric_and_single_character_words(self):
        self.ingest(1, "2026-09-12", "A 로봇 2026", "뉴스 X", "https://words.test/path-7")
        item = read_news(self.db, {"date": ["2026-09-12"]})["items"][0]
        self.assertNotIn("_all_keyword_ids", item)
        self.assertLessEqual(len(item["keywords"]), 6)
        record = read_keyword_record(self.db, item["keyword_record_id"])
        by_label = {word["label"].casefold(): word for word in record["keywords"]}
        for expected in ("a", "x", "2026", "뉴스", "words", "path", "7"):
            self.assertIn(expected, by_label)
        self.assertTrue(by_label["a"]["is_generic"])
        self.assertTrue(by_label["2026"]["is_generic"])
        self.assertEqual(record["word_count"], item["word_count"])
        generic_id = by_label["뉴스"]["id"]
        self.assertNotIn(generic_id, {word["id"] for word in item["keywords"]})
        filtered = read_news(self.db, {"date": ["all"], "keyword": [generic_id]})
        self.assertEqual(len(filtered["items"]), 1)
        self.assertEqual(filtered["keyword_discovery"]["selected_keyword"]["id"], generic_id)

    def test_conservative_normalization_preserves_korean_spelling(self):
        self.ingest(1, "2026-09-12", "OpenAI 오픈AI 비교", "표기 비교", "https://alias.test/a")
        item = read_news(self.db, {"date": ["2026-09-12"]})["items"][0]
        record = read_keyword_record(self.db, item["keyword_record_id"])
        labels = {word["label"] for word in record["keywords"]}
        self.assertIn("OpenAI", labels)
        self.assertIn("오픈AI", labels)
        self.assertNotEqual(self.term(record, "OpenAI")["id"], self.term(record, "오픈AI")["id"])

    def test_unchanged_reconnect_does_not_retokenize(self):
        self.ingest(1, "2026-09-12", "Orion 모델", "성능 평가", "https://cache.test/a")
        self.db.close()
        with patch("keyword_index._words", side_effect=AssertionError("unexpected retokenization")):
            self.db = connect(self.path)
        self.assertEqual(len(read_news(self.db, {"date": ["2026-09-12"]})["items"]), 1)

    def test_old_keyword_schema_is_migrated_without_dropping_terms(self):
        path = Path(self.temp.name) / "old.sqlite3"
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE keyword_terms (
                keyword_id TEXT PRIMARY KEY, normalized TEXT UNIQUE, label TEXT,
                first_seen TEXT, first_observed_at TEXT
            );
            INSERT INTO keyword_terms VALUES ('abc','legacy','Legacy','2026-01-01','2026-01-02T00:00:00Z');
        """)
        init_keyword_index(db)
        columns = {row[1] for row in db.execute("PRAGMA table_info(keyword_terms)")}
        self.assertIn("is_generic", columns)
        self.assertEqual(db.execute("SELECT label FROM keyword_terms WHERE keyword_id='abc'").fetchone()[0],
                         "Legacy")
        db.close()


if __name__ == "__main__":
    unittest.main()
