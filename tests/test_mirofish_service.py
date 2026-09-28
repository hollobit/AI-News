import time

import pytest

import mirofish_service as module
from mirofish_service import LoopbackTransport, MiroFishService


ITEMS = [
    {"title": "AI 정책", "source_url": "https://example.com/a?utm_source=x",
     "day": "2026-09-12", "excerpt": "짧은 발췌", "text": "저장된 전체 메시지"},
]
REQUEST = {"title": "정책 흐름", "requirement": "행위자 관계와 전략을 분석", "rounds": 2,
           "platform": "twitter"}


class FakeTransport:
    def __init__(self, fail_first_graph=False):
        self.calls = []
        self.graph_builds = 0
        self.graph_polls = 0
        self.run_polls = 0
        self.fail_first_graph = fail_first_graph

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        body = kwargs.get("json_body") or {}
        if path == "/api/graph/ontology/generate":
            return {"success": True, "data": {"project_id": "project-1", "ontology": {}}}
        if path == "/api/graph/build":
            self.graph_builds += 1
            return {"success": True, "data": {"task_id": f"graph-task-{self.graph_builds}",
                                                "status": "pending"}}
        if path.startswith("/api/graph/task/"):
            self.graph_polls += 1
            if self.fail_first_graph and "graph-task-1" in path:
                return {"success": True, "data": {"status": "failed", "error": "build failed"}}
            if self.graph_polls % 2:
                return {"success": True, "data": {"status": "processing", "progress": 20}}
            return {"success": True, "data": {"status": "completed",
                                                "result": {"graph_id": "graph-1"}}}
        if path == "/api/simulation/create":
            return {"success": True, "data": {"simulation_id": "simulation-1",
                                                "status": "created"}}
        if path == "/api/simulation/prepare":
            return {"success": True, "data": {"task_id": "prepare-1", "status": "preparing"}}
        if path == "/api/simulation/prepare/status":
            return {"success": True, "data": {"status": "ready"}}
        if path == "/api/simulation/start":
            return {"success": True, "data": {"runner_status": "running"}}
        if path.endswith("/run-status"):
            self.run_polls += 1
            if self.run_polls == 1:
                return {"success": True, "data": {"runner_status": "running",
                                                    "current_round": 1, "progress_percent": 50}}
            if self.run_polls == 2:
                return {"success": True, "data": {"runner_status": "running",
                                                    "current_round": 2, "progress_percent": 100,
                                                    "twitter_running": False,
                                                    "reddit_running": False}}
            return {"success": True, "data": {"runner_status": "completed",
                                                "current_round": 2}}
        if path == "/api/simulation/close-env":
            return {"success": True, "data": {"status": "closing"}}
        if path == "/api/report/generate":
            return {"success": True, "data": {"report_id": "report-1",
                                                "task_id": "report-task-1",
                                                "status": "generating"}}
        if path == "/api/report/generate/status":
            return {"success": True, "data": {"status": "completed"}}
        if path == "/api/report/report-1":
            return {"success": True, "data": {"title": "완료 보고서", "sections": []}}
        if path == "/api/simulation/stop":
            return {"success": False, "pending": True, "error": "stopping"}
        if path == "/api/simulation/interview":
            return {"success": True, "data": {"agent_id": body["agent_id"], "result": "answer"}}
        if path == "/api/report/chat":
            return {"success": True, "data": {"response": "analysis", "sources": []}}
        raise AssertionError(f"unexpected request: {method} {path}")


def wait_for(service, run_id, statuses, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = service.get_run(run_id)
        if run["status"] in statuses:
            return run
        time.sleep(0.005)
    raise AssertionError(f"timed out: {service.get_run(run_id)}")


def make_service(tmp_path, transport=None, **kwargs):
    return MiroFishService(tmp_path / "news.db", transport=transport or FakeTransport(),
                           poll_interval=0, max_polls=20, **kwargs)


def test_draft_snapshot_is_local_deduplicated_complete_and_redacted(tmp_path):
    transport = FakeTransport()
    service = make_service(tmp_path, transport)
    token = "123456789:AA" + "x" * 25
    first = dict(ITEMS[0], text=f"본문 {token}")
    items = [first, dict(first),
             dict(ITEMS[0], text=f"본문 {token}", excerpt="다른 발췌"),
             dict(ITEMS[0], text="변경된 본문")]

    run = service.create_run(items, REQUEST)
    seed = service.seed(run["id"])

    assert run["status"] == "draft"
    assert transport.calls == []
    assert seed["item_count"] == 3
    assert seed["redaction_count"] == 3
    assert all(token not in item["text"] for item in seed["items"])
    markdown = open(run["seed_markdown_path"], encoding="utf-8").read()
    assert "### Message excerpt" in markdown and "### Stored text" in markdown
    assert "짧은 발췌" in markdown and "변경된 본문" in markdown
    assert (tmp_path / "mirofish_runs" / run["id"] / "snapshot.json").stat().st_mode & 0o777 == 0o600
    assert service.list_runs()[0]["id"] == run["id"]
    service.close()


def test_snapshot_rejects_oversize_without_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 100)
    service = make_service(tmp_path)
    with pytest.raises(ValueError, match="최대"):
        service.create_run([dict(ITEMS[0], text="가" * 200)], REQUEST)
    assert service.list_runs() == []
    service.close()


def test_start_requires_runtime_but_draft_creation_does_not(tmp_path):
    service = MiroFishService(tmp_path / "news.db", readiness=lambda: {
        "configured": False, "missing_settings": ["LLM_API_KEY"],
        "backend": {"running": False}}, transport=FakeTransport(), poll_interval=0)
    run = service.create_run(ITEMS, REQUEST)
    assert service.availability()["missing_settings"] == ["LLM_API_KEY"]
    with pytest.raises(RuntimeError, match="설정 또는 백엔드"):
        service.start(run["id"])
    assert service.get_run(run["id"])["status"] == "draft"
    service.close()


def test_round_limit_matches_ui_contract(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(ValueError, match="1~40"):
        service.create_run(ITEMS, dict(REQUEST, rounds=41))
    service.close()


def test_full_workflow_polls_real_routes_and_only_completes_after_report(tmp_path):
    transport = FakeTransport()
    service = make_service(tmp_path, transport)
    run = service.create_run(ITEMS, REQUEST)
    service.start(run["id"])
    final = wait_for(service, run["id"], {"completed", "failed"})

    assert final["status"] == "completed"
    assert (final["project_id"], final["graph_task_id"], final["graph_id"]) == (
        "project-1", "graph-task-1", "graph-1")
    assert (final["simulation_id"], final["report_id"], final["report_task_id"]) == (
        "simulation-1", "report-1", "report-task-1")
    assert final["report"]["title"] == "완료 보고서"
    create = next(call for call in transport.calls if call[1] == "/api/simulation/create")
    assert create[2]["json_body"]["enable_twitter"] is True
    assert create[2]["json_body"]["enable_reddit"] is True
    start = next(call for call in transport.calls if call[1] == "/api/simulation/start")
    assert start[2]["json_body"]["platform"] == "twitter"
    assert any(method == "POST" and path == "/api/simulation/close-env"
               for method, path, _ in transport.calls)
    assert any(method == "POST" and path == "/api/report/generate/status"
               for method, path, _ in transport.calls)
    assert not any("force" in (kwargs.get("json_body") or {}) for _, _, kwargs in transport.calls)
    service.close()


def test_parallel_completion_requires_both_platform_completion_signals():
    one_finished = {"runner_status": "running", "current_round": 40,
                    "token": "irrelevant", "progress_percent": 100,
                    "twitter_current_round": 40, "reddit_current_round": 12,
                    "twitter_completed": True, "reddit_completed": False}
    assert MiroFishService._rounds_finished(one_finished, 40, "parallel") is False
    assert MiroFishService._rounds_finished(
        dict(one_finished, reddit_current_round=40, reddit_completed=True), 40, "parallel") is True
    assert MiroFishService._rounds_finished({"current_round": 40,
                                             "twitter_current_round": 40,
                                             "twitter_completed": False}, 40, "twitter") is False


def test_failed_graph_can_be_explicitly_resumed_with_new_task(tmp_path):
    transport = FakeTransport(fail_first_graph=True)
    service = make_service(tmp_path, transport)
    run = service.create_run(ITEMS, REQUEST)
    service.start(run["id"])
    failed = wait_for(service, run["id"], {"failed"})
    assert failed["stage"] == "graph"

    transport.fail_first_graph = False
    service.resume(run["id"])
    final = wait_for(service, run["id"], {"completed", "failed"})
    assert final["status"] == "completed"
    assert final["graph_task_id"] == "graph-task-2"
    service.close()


def test_restart_marks_inflight_run_paused_without_repeating_side_effects(tmp_path):
    service = make_service(tmp_path)
    run = service.create_run(ITEMS, REQUEST)
    service._update(run["id"], status="running", stage="graph", project_id="project-1",
                    graph_task_id="graph-task-1")
    service.close()

    transport = FakeTransport()
    restarted = make_service(tmp_path, transport)
    paused = restarted.get_run(run["id"])
    assert paused["status"] == "paused"
    assert paused["graph_task_id"] == "graph-task-1"
    assert transport.calls == []
    restarted.close()


class PollingTransport(FakeTransport):
    def request(self, method, path, **kwargs):
        if path.startswith("/api/graph/task/"):
            self.calls.append((method, path, kwargs))
            return {"success": True, "data": {"status": "processing", "progress": 20}}
        return super().request(method, path, **kwargs)


def test_close_wakes_polling_worker_and_leaves_resumable_state(tmp_path):
    transport = PollingTransport()
    service = MiroFishService(tmp_path / "news.db", transport=transport,
                              poll_interval=60, max_polls=100)
    run = service.create_run(ITEMS, REQUEST)
    service.start(run["id"])
    deadline = time.time() + 2
    while time.time() < deadline and not any(
            path.startswith("/api/graph/task/") for _, path, _ in transport.calls):
        time.sleep(0.005)
    service.close()
    paused = wait_for(service, run["id"], {"paused"})
    call_count = len(transport.calls)
    time.sleep(0.03)
    assert paused["stage"] == "graph"
    assert len(transport.calls) == call_count


def test_closed_or_full_service_does_not_mutate_draft_to_queued(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    run = service.create_run(ITEMS, REQUEST)
    service.close()
    with pytest.raises(RuntimeError, match="종료"):
        service.start(run["id"])
    assert service.get_run(run["id"])["status"] == "draft"

    other = make_service(tmp_path / "other")
    queued = other.create_run(ITEMS, REQUEST)
    monkeypatch.setattr(module, "MAX_PENDING_RUNS", 0)
    with pytest.raises(RuntimeError, match="대기열"):
        other.start(queued["id"])
    assert other.get_run(queued["id"])["status"] == "draft"
    other.close()


class StopTransport(FakeTransport):
    def request(self, method, path, **kwargs):
        if path.endswith("/run-status"):
            self.calls.append((method, path, kwargs))
            return {"success": True, "data": {"runner_status": "stopped"}}
        return super().request(method, path, **kwargs)


def test_stop_accepts_pending_response_and_polls_terminal_without_report(tmp_path):
    transport = StopTransport()
    service = make_service(tmp_path, transport)
    run = service.create_run(ITEMS, REQUEST)
    service._update(run["id"], status="paused", stage="simulation_run",
                    project_id="project-1", graph_id="graph-1", simulation_id="simulation-1")
    service.stop(run["id"])
    final = wait_for(service, run["id"], {"stopped", "failed"})
    assert final["status"] == "stopped"
    assert not any(path.startswith("/api/report/") for _, path, _ in transport.calls)
    service.close()


def test_interview_and_report_chat_proxy_validated_run_ids(tmp_path):
    transport = FakeTransport()
    service = make_service(tmp_path, transport)
    run = service.create_run(ITEMS, REQUEST)
    service._update(run["id"], simulation_id="simulation-1")
    assert service.interview(run["id"], 7, "무슨 전략인가?", "twitter")["result"] == "answer"
    assert service.report_chat(run["id"], "근거를 설명해줘", [{"role": "user", "content": "x"}])[
        "response"] == "analysis"
    with pytest.raises(ValueError):
        service.get_run("../bad")
    service.close()


@pytest.mark.parametrize("url", ["https://127.0.0.1:5001", "http://example.com:5001",
                                  "http://user:pass@127.0.0.1:5001"])
def test_transport_only_allows_credential_free_loopback_http(url):
    with pytest.raises(ValueError, match="로컬 HTTP"):
        LoopbackTransport(url)


def test_transport_uses_endpoint_timeouts_and_rejects_redirected_response():
    transport = LoopbackTransport(timeout=30)
    assert transport._timeout_for("/api/graph/ontology/generate") == 900
    assert transport._timeout_for("/api/simulation/interview") == 90
    assert transport._timeout_for("/api/simulation/close-env") == 60
    assert transport._timeout_for("/api/report/chat") == 180
    assert transport._timeout_for("/api/graph/build") == 30

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def geturl(self): return "http://example.com/api/graph/build"
        def read(self, _): return b'{"success":true,"data":{}}'

    class Opener:
        def open(self, *_args, **_kwargs): return Response()

    transport._opener = Opener()
    with pytest.raises(RuntimeError, match="로컬 주소"):
        transport.request("POST", "/api/graph/build", json_body={})
