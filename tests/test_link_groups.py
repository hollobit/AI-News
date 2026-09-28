import unittest

from link_groups import build_link_groups, canonical_url, classify_link, extract_links


def row(message_id, day, text, source_url="", title="AI 자료", topic="models"):
    return {
        "chat_id": "-1001", "message_id": message_id, "item_index": 0,
        "title": title, "excerpt": "상세 설명", "text": text, "day": day,
        "date_basis": "article", "topic": topic, "source_url": source_url,
        "kind": "article", "channel": "news", "url": f"https://t.me/c/1/{message_id}",
        "published_at": f"{day}T09:00:00+09:00", "telegram_day": day,
    }


class LinkGroupTests(unittest.TestCase):
    def test_extracts_markdown_balanced_parens_and_multiple_bare_links(self):
        links = extract_links(
            "[논문](https://example.org/a_(b)?x=1), 참고 https://two.test/p?q=2. "
            "중복 https://two.test/p?q=2"
        )
        self.assertEqual(links, ["https://example.org/a_(b)?x=1", "https://two.test/p?q=2"])

    def test_canonical_known_aliases_and_semantic_queries(self):
        self.assertEqual(
            canonical_url("https://www.Example.com/a/?utm_source=x&id=2&empty=&id=1#part"),
            "https://www.example.com/a?empty=&id=1&id=2#part",
        )
        self.assertNotEqual(canonical_url("https://example.com/a?id=1"),
                            canonical_url("https://example.com/a?id=2"))
        self.assertEqual(canonical_url("https://old.reddit.com/r/ai/comments/ABC123/a-slug/"),
                         "https://reddit.com/comments/abc123")
        self.assertEqual(canonical_url("https://twitter.com/user/status/123?s=1"),
                         "https://x.com/status/123")
        self.assertEqual(canonical_url("https://x.com/i/web/status/123"),
                         "https://x.com/status/123")
        self.assertNotEqual(
            canonical_url("https://reddit.com/r/ai/comments/abc123/slug/comment1"),
            canonical_url("https://reddit.com/r/ai/comments/abc123/slug/comment2"),
        )
        self.assertNotEqual(canonical_url("https://www.unknown.test/a"),
                            canonical_url("https://unknown.test/a"))
        self.assertEqual(canonical_url("https://arxiv.org/pdf/2601.12345v3.pdf"),
                         "https://arxiv.org/abs/2601.12345")
        self.assertEqual(canonical_url("https://youtu.be/abc123?t=4"),
                         "https://youtube.com/watch?t=4&v=abc123")
        self.assertEqual(canonical_url("https://youtu.be/abc123?si=tracking"),
                         "https://youtube.com/watch?v=abc123")
        self.assertEqual(canonical_url("javascript:alert(1)"), "")

    def test_types_are_link_target_types(self):
        cases = {
            "https://arxiv.org/abs/2601.1": "paper",
            "https://simonwillison.net/2026/Jan/1/paper-review/": "blog",
            "https://www.reuters.com/technology/story": "news",
            "https://x.com/a/status/1": "social",
            "https://github.com/org/repo": "tool",
            "https://example.org/page": "other",
            "https://medium.com/person/paper-review": "blog",
            "https://www.reuters.com/articles/research-story": "news",
            "https://www.nature.com/articles/d41586-026-00001": "news",
            "https://www.nature.com/articles/s41586-026-00001": "paper",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                result = classify_link(url, "논문을 소개하는 글", "paper research")
                self.assertEqual(result["content_type"], expected)
                self.assertTrue(result["type_reason"])

    def test_groups_slug_variants_across_days_and_preserves_evidence(self):
        rows = [
            row(1, "2026-09-01", "https://reddit.com/r/ai/comments/abc123/old-slug/?utm_source=x"),
            row(2, "2026-09-03", "https://old.reddit.com/comments/ABC123/new-slug/"),
        ]
        group = build_link_groups(rows)[0]
        self.assertEqual(group["occurrence_count"], 2)
        self.assertEqual(group["distinct_days"], 2)
        self.assertEqual(group["dates"], ["2026-09-01", "2026-09-03"])
        self.assertEqual(group["first_seen"], "2026-09-01")
        self.assertEqual(group["last_seen"], "2026-09-03")
        self.assertEqual(len(group["mentions"]), 2)
        self.assertIn("text", group["mentions"][0])

    def test_duplicate_link_in_one_source_counts_once(self):
        rows = [row(1, "2026-09-01",
                    "https://example.com/a https://example.com/a?utm_medium=telegram",
                    "https://example.com/a")]
        groups = build_link_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["occurrence_count"], 1)
        self.assertEqual(len(groups[0]["variants"]), 2)

    def test_each_group_mention_points_to_its_matching_link(self):
        source = row(1, "2026-09-01",
                     "분석 https://first.test/a 와 https://second.test/b",
                     source_url="https://first.test/a")
        groups = {group["canonical_url"]: group for group in build_link_groups([source])}
        self.assertEqual(groups["https://first.test/a"]["mentions"][0]["source_url"],
                         "https://first.test/a")
        self.assertEqual(groups["https://second.test/b"]["mentions"][0]["source_url"],
                         "https://second.test/b")

    def test_deterministic_ids_and_conservative_related_candidates(self):
        rows = [
            row(1, "2026-09-01", "https://a.test/1", title="OpenAI GPT-5 benchmark results"),
            row(2, "2026-09-02", "https://b.test/2", title="GPT-5 benchmark safety results"),
            row(3, "2026-09-03", "https://c.test/3", title="OpenAI acquires startup"),
        ]
        first = build_link_groups(rows)
        second = build_link_groups(reversed(rows))
        self.assertEqual(first, second)
        by_url = {group["canonical_url"]: group for group in first}
        related = by_url["https://a.test/1"]["related"]
        self.assertEqual([item["canonical_url"] for item in related], ["https://b.test/2"])
        self.assertEqual(len(by_url["https://a.test/1"]["id"]), 24)
        self.assertEqual(len(by_url["https://a.test/1"]["mentions"][0]["id"]), 16)


if __name__ == "__main__":
    unittest.main()
