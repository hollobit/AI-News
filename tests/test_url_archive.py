import json
import sqlite3
import unittest

from url_archive import (archive_message, archived_link_rows, archived_rows, backfill_archive,
                         extract_message_urls, init_archive, reindex_archive_urls)


def db_connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_archive(db)
    return db


def telegram_message(**changes):
    message = {
        "chat": {"id": -100123, "title": "뉴스", "type": "channel", "username": "channel"},
        "message_id": 7, "date": 1_700_000_000,
        "text": "제목\nhttps://example.com/story?utm_source=tg",
    }
    message.update(changes)
    return message


class UrlExtractionTests(unittest.TestCase):
    def test_retains_repeated_plain_urls_and_balanced_parentheses(self):
        found = extract_message_urls(telegram_message(
            text="https://example.com/a_(b) https://example.com/a_(b)"))
        self.assertEqual([item["original_url"] for item in found],
                         ["https://example.com/a_(b)", "https://example.com/a_(b)"])

    def test_utf16_text_link_after_emoji_has_exact_anchor(self):
        text = "😀 연구 논문 보기"
        # Emoji occupies two UTF-16 units; anchor starts after emoji and a space.
        entity = {"type": "text_link", "offset": 3, "length": 8,
                  "url": "https://arxiv.org/abs/2601.00001"}
        found = extract_message_urls(telegram_message(text=text, entities=[entity]))
        self.assertEqual(found[0]["title"], "연구 논문 보기")
        self.assertEqual(found[0]["title_source"], "telegram_anchor")
        self.assertEqual(found[0]["entity_type"], "text_link")
        self.assertEqual(found[0]["verified_article_title"], 0)

    def test_caption_entities_and_markdown_anchor_are_recorded(self):
        caption = "사진 😀 링크"
        entity = {"type": "text_link", "offset": 6, "length": 2,
                  "url": "https://example.org/hidden"}
        found = extract_message_urls(telegram_message(
            text="[공식 발표](https://example.org/post)", caption=caption,
            caption_entities=[entity]))
        self.assertEqual([item["origin"] for item in found],
                         ["text_markdown", "caption_entity"])
        self.assertEqual(found[0]["title"], "공식 발표")
        self.assertEqual(found[1]["title"], "링크")

    def test_adjacent_markdown_links_and_balanced_parentheses_are_separate(self):
        found = extract_message_urls(telegram_message(
            text="[A](https://a.test/a_(x))[B](https://b.test/b)"))
        self.assertEqual([item["original_url"] for item in found],
                         ["https://a.test/a_(x)", "https://b.test/b"])
        self.assertEqual([item["title"] for item in found], ["A", "B"])

    def test_entity_url_and_full_anchor_text_are_preserved_exactly(self):
        long_anchor = "긴" * 120
        text = f"{long_anchor} https://example.org/entity!"
        anchor_units = len(long_anchor.encode("utf-16-le")) // 2
        url = "https://hidden.example/path!"
        entities = [
            {"type": "text_link", "offset": 0, "length": anchor_units, "url": url},
            {"type": "url", "offset": anchor_units + 1, "length": 27},
        ]
        found = extract_message_urls(telegram_message(text=text, entities=entities))
        self.assertEqual(found[0]["title"], long_anchor)
        self.assertEqual(found[0]["original_url"], url)
        self.assertEqual(found[1]["original_url"], "https://example.org/entity!")

    def test_url_entity_is_not_double_counted_as_plain_text(self):
        text = "주소 https://example.org/item"
        entity = {"type": "url", "offset": 3, "length": 24}
        found = extract_message_urls(telegram_message(text=text, entities=[entity]))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["entity_type"], "url")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.db = db_connection()

    def tearDown(self):
        self.db.close()

    def test_edit_preserves_removed_link_history_and_same_version_content_change(self):
        first = telegram_message()
        first_id = archive_message(self.db, first)
        edited = telegram_message(text="새 제목\nhttps://example.com/new", edit_date=1_700_000_010)
        second_id = archive_message(self.db, edited)
        same_timestamp_change = telegram_message(
            text="링크가 제거됨", edit_date=1_700_000_010)
        third_id = archive_message(self.db, same_timestamp_change)
        self.db.commit()

        self.assertEqual(len({first_id, second_id, third_id}), 3)
        snapshots = self.db.execute(
            "SELECT active FROM message_snapshots ORDER BY archived_at,snapshot_id").fetchall()
        self.assertEqual(sum(row[0] for row in snapshots), 1)
        history = archived_rows(self.db, {"active": ["all"]})
        self.assertEqual(history["total"], 2)
        self.assertEqual({item["active"] for item in history["items"]}, {0})

    def test_same_version_content_can_reactivate_an_existing_snapshot(self):
        first = telegram_message()
        first_id = archive_message(self.db, first)
        changed = telegram_message(text="B\nhttps://example.com/b", edit_date=first["date"])
        archive_message(self.db, changed)
        self.assertEqual(archive_message(self.db, first), first_id)

        active = self.db.execute(
            "SELECT snapshot_id FROM message_snapshots WHERE active=1").fetchall()
        self.assertEqual([row[0] for row in active], [first_id])
        active_url = self.db.execute(
            "SELECT original_url FROM archived_urls WHERE active=1").fetchall()
        self.assertEqual([row[0] for row in active_url],
                         ["https://example.com/story?utm_source=tg"])

    def test_new_message_uses_seoul_day(self):
        # 2023-11-14 23:30 UTC is already the next calendar day in Seoul.
        message = telegram_message(date=1_700_004_600)
        archive_message(self.db, message)
        row = self.db.execute("SELECT day,published_at FROM message_snapshots").fetchone()
        self.assertEqual(row["day"], "2023-11-15")
        self.assertTrue(row["published_at"].endswith("+09:00"))

    def test_same_snapshot_is_idempotent_and_payload_is_local_only(self):
        message = telegram_message(entities=[{"type": "bold", "offset": 0, "length": 2}])
        self.assertEqual(archive_message(self.db, message), archive_message(self.db, message))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message_snapshots").fetchone()[0], 1)
        payload = json.loads(self.db.execute("SELECT payload_json FROM message_snapshots").fetchone()[0])
        self.assertEqual(payload["entities"][0]["type"], "bold")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", payload)

    def test_archive_participates_in_caller_transaction(self):
        self.db.execute("BEGIN")
        archive_message(self.db, telegram_message())
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message_snapshots").fetchone()[0], 0)

    def test_safe_paginated_rows_do_not_expose_payload(self):
        archive_message(self.db, telegram_message(text="문맥 제목\nhttps://example.com/one https://example.com/two"))
        result = archived_rows(self.db, {"page_size": ["1"], "q": ["example.com"]})
        self.assertEqual((result["total"], result["page_size"], result["total_pages"]), (2, 1, 2))
        self.assertNotIn("payload_json", result["items"][0])
        self.assertIn("original_url", result["items"][0])
        by_channel = archived_rows(self.db, {"channel": ["-100123"], "status": ["active"]})
        self.assertEqual(by_channel["total"], 2)

        archive_message(self.db, telegram_message(text="수정 후 URL 없음", edit_date=1_700_000_001))
        history = archived_rows(self.db, {"status": ["history"]})
        self.assertEqual(history["total"], 2)

    def test_hidden_link_rows_support_future_group_analysis(self):
        message = telegram_message(text="분석 자료", entities=[{
            "type": "text_link", "offset": 0, "length": 5,
            "url": "https://example.com/hidden",
        }])
        archive_message(self.db, message)
        rows = archived_link_rows(self.db, hidden_only=True)
        self.assertEqual(rows[0]["source_url"], "https://example.com/hidden")
        self.assertEqual(rows[0]["title"], "분석 자료")

    def test_backfill_is_per_message_and_idempotent(self):
        self.db.execute("""CREATE TABLE news (
            chat_id TEXT, message_id INTEGER, channel TEXT, title TEXT, excerpt TEXT,
            text TEXT, url TEXT, published_at TEXT, day TEXT, version INTEGER,
            PRIMARY KEY(chat_id,message_id))""")
        self.db.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)", (
            "-1001", 9, "기존", "제목", "", "제목\nhttps://legacy.example/a",
            "https://t.me/c/1/9", "2026-09-10T12:00:00+09:00", "2026-09-10", 123))
        self.assertEqual(backfill_archive(self.db), 1)
        self.assertEqual(backfill_archive(self.db), 0)
        row = archived_rows(self.db)["items"][0]
        self.assertEqual(row["original_url"], "https://legacy.example/a")
        origin = self.db.execute("SELECT payload_origin FROM message_snapshots").fetchone()[0]
        self.assertEqual(origin, "news_backfill")

    def test_reindex_rebuilds_urls_from_payload_and_keeps_snapshot_state(self):
        first = telegram_message(text="[A](https://a.test/a)[B](https://b.test/b)")
        first_id = archive_message(self.db, first)
        archive_message(self.db, telegram_message(text="현재 링크 없음", edit_date=first["date"] + 1))
        self.db.execute("UPDATE archived_urls SET original_url='https://stale.test/'")

        self.assertEqual(reindex_archive_urls(self.db), 2)
        urls = self.db.execute(
            "SELECT original_url,active FROM archived_urls ORDER BY occurrence").fetchall()
        self.assertEqual([(row[0], row[1]) for row in urls],
                         [("https://a.test/a", 0), ("https://b.test/b", 0)])
        state = self.db.execute(
            "SELECT active FROM message_snapshots WHERE snapshot_id=?", (first_id,)).fetchone()[0]
        self.assertEqual(state, 0)


if __name__ == "__main__":
    unittest.main()
