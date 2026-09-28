import copy
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from semantic import AnalysisService, analyze_group, compare_mentions, input_hash, select_evidence, validate_result


def example_group():
    return {"id": "a"*24, "canonical_url": "https://example.com/story", "title": "합성 테스트 소식",
            "content_type_title": "뉴스 기사", "occurrence_count": 2, "distinct_days": 2,
            "mentions": [
                {"id": "first", "day": "2026-07-01", "date_basis": "article", "published_at": "2026-09-12T10:00:00+09:00",
                 "title": "합성 소식", "excerpt": "첫 번째 설명", "text": "첫 번째 설명", "source_url": "https://example.com/story"},
                {"id": "second", "day": "2026-07-02", "date_basis": "article", "published_at": "2026-09-12T10:01:00+09:00",
                 "title": "합성 소식", "excerpt": "첫 번째 설명", "text": "첫 번째 설명", "source_url": "https://example.com/story"},
            ]}


def example_result():
    return {"summary": "테스트 요약", "key_points": [{"text": "테스트 주장", "evidence_ids": ["first"]}],
            "meaning": [{"text": "테스트 해석", "evidence_ids": ["first", "second"]}],
            "changes": [], "cautions": ["합성 데이터입니다."]}


class SemanticTests(unittest.TestCase):
    def test_repost_is_repetition_not_new_claim(self):
        comparison = compare_mentions(example_group())
        self.assertEqual(comparison["unique_descriptions"], 1)
        self.assertEqual(comparison["repeated_descriptions"], 1)
        self.assertEqual(comparison["changes"][1]["added"], [])

    def test_grounding_rejects_invented_evidence(self):
        result = example_result()
        self.assertEqual(validate_result(result, example_group()["mentions"]), result)
        result["meaning"][0]["evidence_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            validate_result(result, example_group()["mentions"])

    def test_changed_explanation_invalidates_saved_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            service = AnalysisService(Path(folder)/"news.sqlite3", analyzer=lambda group: example_result(), enabled=True)
            group = example_group()
            service.submit(group)
            service.pool.shutdown(wait=True)
            self.assertEqual(service.status(group)["status"], "complete")
            changed = copy.deepcopy(group)
            changed["mentions"][1]["text"] = "수정된 설명"
            self.assertNotEqual(input_hash(group), input_hash(changed))
            self.assertEqual(service.status(changed)["status"], "stale")
            service.close()

    def test_changed_input_while_running_is_automatically_analyzed(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def analyze(group):
            seen.append(group["mentions"][0]["text"])
            started.set()
            self.assertTrue(release.wait(5))
            return example_result()
        with tempfile.TemporaryDirectory() as folder:
            service = AnalysisService(Path(folder)/"news.sqlite3", analyzer=analyze, enabled=True)
            group = example_group()
            service.submit(group)
            self.assertTrue(started.wait(5))
            changed = copy.deepcopy(group)
            changed["mentions"][0]["text"] = "수정된 설명"
            self.assertEqual(service.status(changed)["status"], "running")
            self.assertTrue(service.status(changed)["outdated"])
            self.assertEqual(service.submit(changed), "queued")
            release.set()
            service.pool.shutdown(wait=True)
            self.assertEqual(seen, [group["mentions"][0]["text"], "수정된 설명"])
            self.assertEqual(service.status(changed)["status"], "complete")

    def test_close_cancels_pending_snapshot(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def analyze(group):
            seen.append(group["mentions"][0]["text"])
            started.set()
            self.assertTrue(release.wait(5))
            return example_result()
        with tempfile.TemporaryDirectory() as folder:
            service = AnalysisService(Path(folder)/"news.sqlite3", analyzer=analyze, enabled=True)
            group = example_group()
            service.submit(group)
            self.assertTrue(started.wait(5))
            changed = copy.deepcopy(group)
            changed["mentions"][0]["text"] = "수정된 설명"
            service.submit(changed)
            service.close()
            release.set()
            service.pool.shutdown(wait=True)
            self.assertEqual(len(seen), 1)
            with self.assertRaises(RuntimeError):
                service.submit(changed)

    def test_changed_input_after_failure_is_stale(self):
        def fail(group):
            raise RuntimeError("합성 실패")
        with tempfile.TemporaryDirectory() as folder:
            service = AnalysisService(Path(folder)/"news.sqlite3", analyzer=fail, enabled=True)
            group = example_group()
            service.submit(group)
            service.pool.shutdown(wait=True)
            self.assertEqual(service.status(group)["status"], "failed")
            group["mentions"][0]["text"] = "수정"
            self.assertEqual(service.status(group)["status"], "stale")

    def test_external_analysis_disabled_without_permission(self):
        with patch.dict(os.environ, {}, clear=True), patch("semantic.subprocess.run") as runner:
            with self.assertRaises(RuntimeError):
                analyze_group(example_group())
            runner.assert_not_called()
            with tempfile.TemporaryDirectory() as folder:
                service = AnalysisService(Path(folder)/"news.sqlite3")
                self.assertFalse(service.status(example_group())["enabled"])
                with self.assertRaises(RuntimeError):
                    service.submit(example_group())
                service.close()

    def test_sampling_includes_first_and_last_dates(self):
        group = example_group()
        group["mentions"] = [dict(group["mentions"][0], id=str(i), day=f"2026-07-{i+1:02d}", text=f"설명 {i}", excerpt=f"설명 {i}")
                             for i in range(20)]
        selected = select_evidence(group, limit=6)
        self.assertEqual(len(selected), 6)
        self.assertEqual(selected[0]["day"], "2026-07-01")
        self.assertEqual(selected[-1]["day"], "2026-07-20")


if __name__ == "__main__":
    unittest.main()
