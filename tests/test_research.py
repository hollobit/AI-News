import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from research import (ResearchService, freeze_documents, select_message_evidence, validate_document_batch,
                      synthesis_card, REDUCE_SIZE, REPORT_SCHEMA)


def report_for(ids):
    evidence = list(ids)
    return {
        "summary": "전체 흐름 요약",
        "major_topics": [{"name": "AI", "weight": 1.0, "meaning": "채널 내 핵심 주제", "evidence_doc_ids": evidence}],
        "players": [{"name": "Example", "type": "company", "role": "개발", "evidence_doc_ids": evidence}],
        "relationships": [{"source": "Example", "target": "한국", "type": "시장", "meaning": "진출 검토", "evidence_doc_ids": evidence}],
        "country_strategies": [{"name": "한국", "assessment": "조건부 투자", "evidence_doc_ids": evidence}],
        "company_strategies": [{"name": "Example", "assessment": "제품 확대", "evidence_doc_ids": evidence}],
        "outlooks": {period: [{"scenario": "기본", "assessment": "조건부 전망", "assumptions": ["현재 흐름 유지"],
                               "signals": ["발표 증가"], "risks": ["근거 부족"], "evidence_doc_ids": evidence}]
                     for period in ("short", "medium", "long")},
        "covered_doc_ids": evidence,
    }


class SyntheticAnalyzer:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, prompt, schema):
        self.calls.append(prompt)
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("합성 배치 실패")
        if prompt.startswith("Analyze every"):
            payload = json.loads(prompt.split("\nDATA:\n", 1)[1])
            analyses = []
            ids = [item["doc_id"] for item in payload]
            for item in payload:
                doc_id = item["doc_id"]
                analyses.append({
                    "doc_id": doc_id, "summary": "문서 요약",
                    "key_points": [{"text": "핵심", "evidence_doc_ids": [doc_id]}],
                    "topics": ["AI"], "countries": ["한국"],
                    "players": [{"name": "Example", "type": "company", "role": "개발"}],
                    "relations": [{"source": "Example", "target": "한국", "type": "시장",
                                   "meaning": "진출", "evidence_doc_ids": [doc_id]}],
                    "implications": [{"text": "의미", "evidence_doc_ids": [doc_id]}],
                })
            return {"analyses": analyses}
        marker = prompt.split("EXPECTED_DOC_IDS:", 1)[1].split("\nDATA:", 1)[0]
        return report_for(json.loads(marker))


def wait_done(service, run_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = service.get_run(run_id)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("research did not finish")


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "news.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def rows(count=1):
        return [{"chat_id": "secret", "message_id": index + 1, "item_index": 0,
                 "title": f"소식 {index}", "text": f"AI 발표 {index}",
                 "source_url": f"https://example.com/story/{index}", "day": "2026-09-01",
                 "published_at": "2026-09-01T00:00:00+09:00", "channel": "news",
                 "topic": "models", "date_basis": "article"} for index in range(count)]

    def service(self, analyzer=None, fetcher=None):
        return ResearchService(
            self.path,
            fetcher=fetcher or (lambda url: {"status": "fetched", "title": "원문", "text": "원문 본문",
                                                   "truncated": False}),
            analyzer=analyzer or SyntheticAnalyzer(), enabled=True)

    def test_freeze_groups_variants_titles_and_keeps_url_free_document(self):
        rows = self.rows()
        rows.append(dict(rows[0], source_url="", title="링크 없는 공지", text="독립 공지", message_id=9))
        archive = [
            {"original_url": "https://example.com/story/0?utm_source=x", "title": "표시 제목 A",
             "nearby_context": "문맥 A", "day": "2026-09-01", "message_id": 1},
            {"original_url": "https://example.com/story/0", "title": "표시 제목 B",
             "nearby_context": "문맥 B", "day": "2026-09-02", "message_id": 2},
        ]
        documents = freeze_documents(rows, archive)
        self.assertEqual(len(documents), 2)
        linked = next(item for item in documents if item["kind"] == "url")
        self.assertEqual(len(linked["variants"]), 2)
        self.assertIn("표시 제목 A", linked["titles"])
        self.assertNotIn("chat_id", json.dumps(linked["metadata"]))
        self.assertEqual(len(linked["evidence"]), 3)
        self.assertEqual(next(item for item in documents if item["kind"] == "text")["canonical_url"], "")

    def test_full_message_evidence_is_preserved_while_model_selection_is_bounded(self):
        rows = [dict(self.rows()[0], message_id=index, day=f"2026-09-{index:02d}",
                     text=(f"변경 설명 {index} " + "가" * 1800)) for index in range(1, 9)]
        document = freeze_documents(rows, [])[0]
        self.assertEqual(len(document["evidence"]), 8)
        self.assertGreater(sum(len(item["text"]) for item in document["evidence"]), 14000)
        selected, truncated = select_message_evidence(document["evidence"])
        self.assertLessEqual(len(selected), 6)
        self.assertLessEqual(sum(len(item["text"]) for item in selected), 3500)
        self.assertTrue(truncated)
        self.assertEqual(selected[0]["metadata"]["day"], "2026-09-01")
        self.assertEqual(selected[-1]["metadata"]["day"], "2026-09-08")

    def test_run_is_frozen_and_reports_complete_coverage(self):
        analyzer = SyntheticAnalyzer()
        service = self.service(analyzer=analyzer)
        run = service.create_run(self.rows(3), [])
        # Mutating the caller's later corpus cannot alter the persisted snapshot.
        later = self.rows(4)
        self.assertEqual(len(later), 4)
        done = wait_done(service, run["id"])
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["total_documents"], 3)
        self.assertEqual(done["coverage"]["analysis_coverage"], 1.0)
        self.assertEqual(done["coverage"]["url_documents"], 3)
        self.assertEqual(set(done["report"]["covered_doc_ids"]),
                         {item["doc_id"] for item in service.documents(run["id"], page_size=100)["documents"]})
        self.assertEqual(done["metrics"]["topic_weights"][0]["count"], 3)
        self.assertEqual(done["metrics"]["daily_topic_weights"][0]["share"], 1.0)
        self.assertEqual(done["metrics"]["country_mentions"][0]["name"], "한국")
        service.close()

    def test_document_batch_rejects_silent_omission_and_duplicate(self):
        analyzer = SyntheticAnalyzer()
        rows = self.rows(2)
        documents = freeze_documents(rows, [])
        ids = [item["doc_id"] for item in documents]
        service = self.service(analyzer=analyzer)
        # Generate a valid synthetic result, then prove exact-coverage validation.
        prepared = []
        for item in documents:
            prepared.append({**item, "titles_json": json.dumps(item["titles"]),
                             "metadata_json": json.dumps(item["metadata"]),
                             "evidence_json": json.dumps(item["evidence"]),
                             "source_status": "fetched", "source_title": "", "source_text": "", "source_truncated": False})
        result = analyzer(service._document_prompt(prepared), None)
        with self.assertRaises(ValueError):
            validate_document_batch({"analyses": result["analyses"][:1]}, ids)
        duplicated = [result["analyses"][0], result["analyses"][0]]
        with self.assertRaises(ValueError):
            validate_document_batch({"analyses": duplicated}, ids)
        service.close()

    def test_fetch_failure_falls_back_to_message_and_is_disclosed(self):
        captured = []
        analyzer = SyntheticAnalyzer()
        def recording(prompt, schema):
            captured.append(prompt)
            return analyzer(prompt, schema)
        service = self.service(analyzer=recording,
                               fetcher=lambda url: {"status": "blocked", "title": "", "text": "",
                                                    "error": "robots", "truncated": False})
        run = service.create_run(self.rows(), [])
        done = wait_done(service, run["id"])
        document = service.documents(run["id"])["documents"][0]
        self.assertEqual(done["status"], "complete")
        self.assertEqual(document["source_status"], "blocked")
        self.assertEqual(done["coverage"]["source_failed"], 1)
        self.assertIn('"message_excerpt": "AI 발표 0"', captured[0])
        service.close()

    def test_failed_batch_is_explicit_and_resume_retries_exact_documents(self):
        analyzer = SyntheticAnalyzer(fail_first=True)
        service = self.service(analyzer=analyzer)
        run = service.create_run(self.rows(2), [])
        failed = wait_done(service, run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failed_documents"], 2)
        service.close()
        resumed = self.service(analyzer=analyzer)
        resumed.resume(run["id"])
        done = wait_done(resumed, run["id"])
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["successful_documents"], 2)
        resumed.close()

    def test_close_pauses_between_external_calls_and_new_service_resumes(self):
        started, release = threading.Event(), threading.Event()
        def slow_fetch(url):
            started.set()
            release.wait(2)
            return {"status": "fetched", "title": "원문", "text": "본문", "truncated": False}
        service = self.service(fetcher=slow_fetch)
        run = service.create_run(self.rows(2), [])
        self.assertTrue(started.wait(2))
        threading.Timer(0.05, release.set).start()
        service.close()
        self.assertEqual(service.get_run(run["id"])["status"], "paused")
        resumed = self.service()
        resumed.resume(run["id"])
        done = wait_done(resumed, run["id"])
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["successful_documents"], 2)
        resumed.close()

    def test_map_reduce_covers_every_document_across_batches(self):
        analyzer = SyntheticAnalyzer()
        service = self.service(analyzer=analyzer)
        run = service.create_run(self.rows(19), [])
        done = wait_done(service, run["id"])
        self.assertEqual(done["status"], "complete")
        self.assertEqual(len(done["report"]["covered_doc_ids"]), 19)
        document_calls = sum(call.startswith("Analyze every") for call in analyzer.calls)
        synthesis_calls = len(analyzer.calls) - document_calls
        self.assertEqual(document_calls, 3)
        self.assertGreaterEqual(synthesis_calls, 2)
        for call in analyzer.calls:
            if not call.startswith('Analyze every'):
                cards = json.loads(call.split('\nDATA:', 1)[1])
                self.assertLessEqual(len(cards), REDUCE_SIZE)
        service.close()

    def test_synthesis_cards_preserve_exact_ids_and_disclose_compaction(self):
        ids = ['url_' + str(i) * 30 for i in range(20)]
        report = report_for(ids)
        report['summary'] = '원문 설명' * 500
        report['players'] = report['players'] * 20
        card = synthesis_card(report)
        self.assertEqual(card['covered_doc_ids'], ids)
        self.assertEqual(card['major_topics'][0]['evidence_doc_ids'], ids)
        self.assertLessEqual(len(card['summary']), 360)
        self.assertLessEqual(len(card['players']), 8)
        self.assertIn('summary', card['input_scope']['omitted_or_shortened_fields'])
        self.assertEqual(len(report['players']), 20)
        prompt = ResearchService._synthesis_prompt([report], ids, final=False)
        self.assertIn('MUST be empty arrays', prompt)
        self.assertIn('1800 Korean characters', prompt)
        self.assertEqual(REPORT_SCHEMA['properties']['relationships']['maxItems'], 8)

    def test_synthesis_timeout_is_recorded_and_resume_reuses_document_work(self):
        synthetic = SyntheticAnalyzer()
        failed_once = False

        def analyzer(prompt, schema):
            nonlocal failed_once
            if not prompt.startswith('Analyze every') and not failed_once:
                failed_once = True
                raise RuntimeError('분석 시간이 초과되었습니다.')
            return synthetic(prompt, schema)

        service = self.service(analyzer=analyzer)
        run = service.create_run(self.rows(3), [])
        failed = wait_done(service, run['id'])
        self.assertEqual(failed['status'], 'failed')
        db = service.database()
        try:
            batch = db.execute("SELECT status,error FROM research_batches WHERE run_id=? AND stage='synthesis'", (run['id'],)).fetchone()
            self.assertEqual(batch['status'], 'failed')
            self.assertIn('초과', batch['error'])
        finally:
            db.close()
        service.thread.join(2)
        service.resume(run['id'])
        done = wait_done(service, run['id'])
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(sum(p.startswith('Analyze every') for p in synthetic.calls), 1)
        self.assertEqual(len(done['report']['covered_doc_ids']), 3)
        service.close()

    def test_disabled_service_does_not_create_a_run(self):
        service = ResearchService(self.path, fetcher=lambda url: {}, analyzer=SyntheticAnalyzer(), enabled=False)
        with self.assertRaises(RuntimeError):
            service.create_run(self.rows(), [])
        self.assertEqual(service.list_runs(), [])
        service.close()


if __name__ == "__main__":
    unittest.main()
