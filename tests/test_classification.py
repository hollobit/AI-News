import unittest

from classification import TOPICS, classify_message


def row(text, day="2026-09-12"):
    return {
        "text": text,
        "title": text.splitlines()[0],
        "excerpt": "",
        "url": "https://t.me/c/4402623886/1",
        "published_at": f"{day}T09:00:00+09:00",
        "day": day,
    }


class ClassificationTests(unittest.TestCase):
    def test_topic_contract_and_specialist_categories(self):
        self.assertEqual(list(TOPICS), [
            "agents", "models", "robotics", "medical", "hardware",
            "policy", "business", "research", "general",
        ])
        medical = classify_message(row("의료 브리핑\n신약 임상 환자 진단 소식"))[0]
        robotics = classify_message(row("Robotics 브리핑\n테슬라 로보택시와 VLA 로봇"))[0]
        self.assertEqual(medical["topic"], "medical")
        self.assertEqual(robotics["topic"], "robotics")

    def test_full_and_short_item_dates_split_with_continuation_link(self):
        result = classify_message(row("""📰 AI 뉴스 (2026-07-06 06:01)
Cronjob Response: AI 뉴스 브리핑
(jobid: abc123)
-------------
- [2026-07-05] **Fugu agent orchestrator released** — 멀티에이전트 도구입니다.
  출처: [공식 저장소](https://github.com/SakanaAI/fugu)
- [7/04] **의료 AI 진단 연구** — 임상 연구 결과입니다.
  출처: Journal | https://example.org/paper

📧 이메일 발송 완료 (news@example.com)
To stop or manage this job, send me a new message."""))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["day"], "2026-07-05")
        self.assertEqual(result[0]["date_basis"], "article")
        self.assertEqual(result[0]["source_url"], "https://github.com/SakanaAI/fugu")
        self.assertNotIn("Cronjob", result[0]["text"])
        self.assertNotIn("이메일 발송", result[-1]["text"])
        self.assertEqual(result[1]["day"], "2026-07-04")
        self.assertEqual(result[1]["topic"], "medical")

    def test_short_date_uses_nearest_year_at_year_boundary(self):
        new_year = classify_message(row("""AI 뉴스 브리핑 | 2026-01-01
- [12/31] 작년 로봇 뉴스
  https://example.com/robot"""))[0]
        year_end = classify_message(row("""AI 뉴스 브리핑 | 2026-12-31
- [1/1] 새해 모델 뉴스
  https://example.com/model"""))[0]
        self.assertEqual(new_year["day"], "2025-12-31")
        self.assertEqual(year_end["day"], "2027-01-01")

    def test_short_date_without_message_year_falls_back_to_telegram(self):
        result = classify_message(row("- [7/04] 로봇 출시\n  https://example.com/robot"))[0]
        self.assertEqual(result["day"], "2026-09-12")
        self.assertEqual(result["date_basis"], "telegram")

    def test_interleaved_delivery_does_not_inherit_another_message_date(self):
        historical = classify_message(row("AI 뉴스 브리핑 | 2026-07-04\n오늘 소식"))[0]
        undated = classify_message(row("독립 메시지\n새로운 소식", day="2026-09-12"))[0]
        self.assertEqual(historical["day"], "2026-07-04")
        self.assertEqual(historical["date_basis"], "briefing")
        self.assertEqual(undated["day"], "2026-09-12")
        self.assertEqual(undated["date_basis"], "telegram")

    def test_numbered_summary_without_links_is_one_briefing(self):
        result = classify_message(row("""오늘 핵심 요약
1. Anthropic 에이전트 도입이 늘었습니다.
2. DeepSeek 모델 출시가 임박했습니다.
📧 이메일도 news@example.com으로 발송 완료했습니다."""))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "briefing")
        self.assertNotIn("이메일", result[0]["text"])

    def test_credible_undated_bullet_can_be_an_article(self):
        result = classify_message(row(
            "- Open source model released — 개발사가 새 가중치를 공개했습니다."
        ))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "article")
        self.assertEqual(result[0]["date_basis"], "telegram")

    def test_safe_url_and_english_ai_word_boundary(self):
        unsafe = classify_message(row("""- 이메일 서비스 출시 — 일반 업무 도구
  [열기](javascript:alert(1))"""))[0]
        safe = classify_message(row("""- AI model released
  https://example.com/news)."""))[0]
        self.assertEqual(unsafe["source_url"], "")
        self.assertEqual(unsafe["topic"], "general")
        self.assertEqual(safe["source_url"], "https://example.com/news")
        self.assertEqual(safe["topic"], "models")

    def test_last_article_stops_before_sections_and_recap(self):
        result = classify_message(row("""📰 AI 뉴스 (2026-07-06 06:01)
- [2026-07-05] **MCP tool released** — 에이전트를 연결하는 개발 도구입니다.
  링크: https://example.com/mcp

---

**📡 arXiv**
• 주말 미발행으로 새 논문이 없습니다.

📌 이번 주 핵심 요약
1. 에이전트 도구가 늘었습니다.
📧 이메일 발송 완료했습니다."""))
        self.assertEqual(len(result), 1)
        self.assertNotIn("arXiv", result[0]["text"])
        self.assertNotIn("핵심 요약", result[0]["text"])
        self.assertNotIn("이메일", result[0]["text"])
        self.assertEqual(result[0]["excerpt"], "에이전트를 연결하는 개발 도구입니다.")

    def test_mcp_server_is_agent_tooling_but_gpu_server_is_hardware(self):
        mcp = classify_message(row(
            "- Turn your agent into an MCP Server — ChatGPT 개발 도구\n"
            "  https://example.com/mcp"
        ))[0]
        gpu = classify_message(row(
            "- GPU 서버용 HBM 반도체 공개 — 데이터센터 인프라 제품\n"
            "  https://example.com/gpu"
        ))[0]
        self.assertEqual(mcp["topic"], "agents")
        self.assertEqual(gpu["topic"], "hardware")

    def test_invalid_item_date_falls_back_to_briefing_date(self):
        result = classify_message(row("""📰 AI 뉴스 (2026-07-06 08:00)
- [2026-02-30] 보안 규제 소식
  https://example.com/policy"""))[0]
        self.assertEqual(result["day"], "2026-07-06")
        self.assertEqual(result["date_basis"], "briefing")
        self.assertEqual(result["topic"], "policy")

    def test_plain_and_truncated_messages_are_preserved(self):
        plain = classify_message(row("한 줄짜리 일반 소식"))[0]
        truncated = classify_message(row("""📰 **AI Safety** (2026-07-06 08:02)
Cronjob Response: AI Safety 브리핑 (아침 8시)
(job_id: abc)
-------------
🛡️ **AI Safety"""))[0]
        self.assertEqual(plain["kind"], "briefing")
        self.assertEqual(plain["text"], "한 줄짜리 일반 소식")
        self.assertEqual(truncated["day"], "2026-07-06")
        self.assertIn("AI Safety", truncated["text"])
        self.assertNotIn("job_id", truncated["text"])

    def test_historical_date_in_body_is_not_a_header(self):
        result = classify_message(row(
            "일반 메모\n과거 2024-01-02 사건을 돌아봅니다.", day="2026-09-12"
        ))[0]
        self.assertEqual(result["day"], "2026-09-12")
        self.assertEqual(result["date_basis"], "telegram")

    def test_dated_news_url_is_neither_header_nor_year_context(self):
        result = classify_message(row("""출처: https://example.com/tech/tech-news/2025/12/18/story
- [7/04] 로봇 출시
  https://example.com/robot
- 새 반도체 발표 — GPU 서버 공급을 확대합니다.
  https://example.com/chip"""))
        self.assertEqual(len(result), 2)
        self.assertEqual({item["day"] for item in result}, {"2026-09-12"})
        self.assertEqual({item["date_basis"] for item in result}, {"telegram"})

    def test_scanning_for_header_stops_at_first_article(self):
        result = classify_message(row("""- 첫 기사 — 충분히 긴 기사 설명입니다.
  https://example.com/first
AI 뉴스 브리핑 | 2025-12-18"""))[0]
        self.assertEqual(result["day"], "2026-09-12")
        self.assertEqual(result["date_basis"], "telegram")

    def test_model_version_inside_title_is_not_a_date(self):
        result = classify_message(row("""📰 AI 뉴스 (2026-07-05 06:02)
- Model 3-4 released — 새로운 모델을 공개했습니다.
  https://example.com/model"""))[0]
        self.assertEqual(result["day"], "2026-07-05")
        self.assertEqual(result["date_basis"], "briefing")


if __name__ == "__main__":
    unittest.main()

class HierarchicalBriefingTests(unittest.TestCase):
    def test_trailing_source_uses_section_heading_not_last_related_story(self):
        for marker in ('3️⃣', '3.', '3)'):
            text = f'''뉴스 브리핑 | 2026-10-01
{marker} [공공AX] 포티투마루, 9개국에 공공 AX 전략 제시
- 네이버, 멕시코 정부 협력 제안
- 고려대 세종캠, 전교생 AI 교육으로 AX 인재 양성
🔗 https://www.epnc.co.kr/news/articleView.html?idxno=407591

4️⃣ [AI챔피언] 전국 공공기관 인증 소식
- 별도 소식
🔗 https://example.org/other?id=5'''
            result = classify_message(row(text))
            self.assertEqual(len(result), 2)
            self.assertIn('포티투마루', result[0]['title'])
            self.assertNotIn('고려대', result[0]['title'])
            self.assertNotIn('AI챔피언', result[0]['text'])
            self.assertEqual(result[0]['source_url'], 'https://www.epnc.co.kr/news/articleView.html?idxno=407591')
            self.assertIn('AI챔피언', result[1]['title'])

    def test_section_does_not_override_individually_linked_bullet_titles(self):
        result = classify_message(row('''1️⃣ 여러 소식
- 첫 번째 독립 기사
https://example.org/first
- 두 번째 독립 기사
https://example.org/second
2️⃣ 기타 소식'''))
        self.assertEqual([a['title'] for a in result], ['첫 번째 독립 기사', '두 번째 독립 기사'])
        self.assertNotIn('기타 소식', result[-1]['text'])
