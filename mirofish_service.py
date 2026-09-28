"""Persistent orchestration for a real loopback MiroFish runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from link_groups import canonical_url


MAX_ITEMS = 5000
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_ROUNDS = 40
MAX_PENDING_RUNS = 20
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_UPSTREAM_ID = re.compile(r"[A-Za-z0-9_.-]{1,200}")
_SECRET = re.compile(
    r"(?:\b\d{7,12}:AA[A-Za-z0-9_-]{20,}\b|\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"(?i:(?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+)"
)
_ACTIVE = {"queued", "running", "stop_requested"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _redact(value) -> tuple[str, int]:
    count = 0

    def replace(_match):
        nonlocal count
        count += 1
        return "[REDACTED]"

    return _SECRET.sub(replace, str(value or "")), count


def _safe_error(error) -> str:
    message, _ = _redact(str(error or "MiroFish 요청 실패"))
    message = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "http://[REDACTED]@", message)
    return _clean(message)[:500] or "MiroFish 요청 실패"


class LoopbackTransport:
    """Small JSON/multipart HTTP client restricted to local MiroFish."""

    def __init__(self, base_url="http://127.0.0.1:5001", timeout=30):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            raise ValueError("MiroFish backend_url은 로컬 HTTP 주소만 허용합니다.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def _timeout_for(self, path):
        if path == "/api/graph/ontology/generate":
            return max(self.timeout, 900)
        if path == "/api/simulation/interview":
            return max(self.timeout, 90)
        if path == "/api/simulation/close-env":
            return max(self.timeout, 60)
        if path == "/api/report/chat":
            return max(self.timeout, 180)
        return self.timeout

    def request(self, method, path, json_body=None, fields=None, files=None):
        if not re.fullmatch(r"/api/[A-Za-z0-9_./?=&%-]+", path) or ".." in path:
            raise ValueError("허용되지 않은 MiroFish API 경로입니다.")
        headers = {"Accept": "application/json"}
        data = None
        if files:
            boundary = "----news-mirofish-" + uuid4().hex
            chunks = []
            for key, value in (fields or {}).items():
                chunks.extend([
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"), b"\r\n",
                ])
            for key, (filename, content, content_type) in files.items():
                chunks.extend([
                    f"--{boundary}\r\n".encode(),
                    (f'Content-Disposition: form-data; name="{key}"; '
                     f'filename="{filename}"\r\n').encode(),
                    f"Content-Type: {content_type}\r\n\r\n".encode(), content, b"\r\n",
                ])
            chunks.append(f"--{boundary}--\r\n".encode())
            data = b"".join(chunks)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout_for(path)) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "http" or final.hostname not in {"127.0.0.1", "localhost"}:
                    raise RuntimeError("MiroFish 응답이 로컬 주소를 벗어났습니다.")
                payload = response.read(8 * 1024 * 1024 + 1)
                if len(payload) > 8 * 1024 * 1024:
                    raise RuntimeError("MiroFish 응답이 허용 크기를 초과했습니다.")
        except HTTPError as error:
            raise RuntimeError(f"MiroFish HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"MiroFish 연결 실패: {_safe_error(error.reason if hasattr(error, 'reason') else error)}") from None
        try:
            result = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("MiroFish가 JSON이 아닌 응답을 반환했습니다.") from None
        if not isinstance(result, dict):
            raise RuntimeError("MiroFish 응답 형식이 올바르지 않습니다.")
        return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MiroFishService:
    def __init__(self, db_path, backend_url="http://127.0.0.1:5001", readiness=None,
                 transport=None, poll_interval=2.0, max_polls=1800):
        self.db_path = str(db_path)
        self.artifact_root = Path(db_path).parent / "mirofish_runs"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.backend_url = backend_url
        self._readiness = readiness
        self._transport_injected = transport is not None
        self.transport = transport or LoopbackTransport(backend_url)
        self.poll_interval = max(0, float(poll_interval))
        self.max_polls = max(1, int(max_polls))
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._jobs = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mirofish")
        self._closed = False
        self._init_db()
        self._pause_interrupted()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mirofish_runs (
                    id TEXT PRIMARY KEY,status TEXT NOT NULL,stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,title TEXT NOT NULL,requirement TEXT NOT NULL,
                    rounds INTEGER NOT NULL,platform TEXT NOT NULL,item_count INTEGER NOT NULL,
                    snapshot_bytes INTEGER NOT NULL,seed_markdown_path TEXT NOT NULL,
                    snapshot_json_path TEXT NOT NULL,project_id TEXT,graph_task_id TEXT,
                    graph_id TEXT,prepare_task_id TEXT,simulation_id TEXT,report_id TEXT,
                    report_task_id TEXT,close_requested INTEGER NOT NULL DEFAULT 0,
                    error TEXT,result TEXT,report TEXT,created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,started_at TEXT,completed_at TEXT
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(mirofish_runs)")}
            if "report_task_id" not in columns:
                db.execute("ALTER TABLE mirofish_runs ADD COLUMN report_task_id TEXT")
            if "close_requested" not in columns:
                db.execute("ALTER TABLE mirofish_runs ADD COLUMN close_requested INTEGER NOT NULL DEFAULT 0")

    def _pause_interrupted(self):
        with self._connect() as db:
            db.execute("""UPDATE mirofish_runs SET status='paused',
                error='로컬 서비스 재시작으로 일시 중지됨',updated_at=?
                WHERE status IN ('queued','running','stop_requested')""", (_now(),))

    @staticmethod
    def _validate_id(run_id):
        if not _RUN_ID.fullmatch(str(run_id or "")):
            raise ValueError("실행 ID 형식이 올바르지 않습니다.")
        return str(run_id)

    @staticmethod
    def _upstream_id(value, label):
        value = str(value or "")
        if not _UPSTREAM_ID.fullmatch(value):
            raise RuntimeError(f"MiroFish가 올바른 {label}를 반환하지 않았습니다.")
        return value

    def availability(self):
        if self._readiness is None:
            if self._transport_injected:
                return {"configured": True, "available": True,
                        "backend_url": self.backend_url, "reason": "테스트 전송기 사용"}
            return {"configured": False, "available": False, "backend_url": self.backend_url,
                    "reason": "MiroFish 런타임 상태 확인 함수가 연결되지 않았습니다.",
                    "missing_settings": []}
        try:
            value = self._readiness()
        except Exception as error:
            return {"configured": False, "available": False, "backend_url": self.backend_url,
                    "reason": _safe_error(error), "missing_settings": []}
        if isinstance(value, bool):
            return {"configured": value, "available": value, "backend_url": self.backend_url,
                    "reason": "준비됨" if value else "MiroFish 런타임이 준비되지 않았습니다."}
        value = dict(value or {})
        backend = value.get("backend") if isinstance(value.get("backend"), dict) else {}
        configured = bool(value.get("configured"))
        available = configured and bool(backend.get("running", value.get("available", False)))
        return {"configured": configured, "available": available,
                "backend_url": backend.get("url") or self.backend_url,
                "reason": "준비됨" if available else "MiroFish 설정 또는 백엔드 실행이 필요합니다.",
                "missing_settings": list(value.get("missing_settings") or [])}

    @staticmethod
    def _snapshot(items):
        if not isinstance(items, list) or not items:
            raise ValueError("분석할 뉴스가 필요합니다.")
        if len(items) > MAX_ITEMS:
            raise ValueError(f"뉴스는 최대 {MAX_ITEMS}건까지 한 실행에 포함할 수 있습니다.")
        result, seen, redactions = [], set(), 0
        for item in items:
            title, count = _redact(item.get("title")); redactions += count
            excerpt, count = _redact(item.get("excerpt")); redactions += count
            text, count = _redact(item.get("text")); redactions += count
            source_url, count = _redact(item.get("source_url")); redactions += count
            source_url = str(source_url).strip()
            if source_url:
                parsed = urlsplit(source_url)
                if parsed.username or parsed.password:
                    raise ValueError("사용자 정보가 포함된 URL은 스냅샷에 저장할 수 없습니다.")
            canonical = canonical_url(source_url)
            record = {"title": _clean(title), "url": canonical or source_url,
                      "day": str(item.get("day") or ""), "excerpt": _clean(excerpt),
                      "text": str(text or "").strip()}
            key = (record["url"], record["title"].casefold(), record["day"],
                   record["excerpt"].casefold(), _clean(record["text"]).casefold())
            if key in seen:
                continue
            seen.add(key); result.append(record)
        payload = {"items": result, "item_count": len(result), "redaction_count": redactions}
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError(f"스냅샷은 최대 {MAX_SNAPSHOT_BYTES // (1024*1024)}MB까지 허용합니다.")
        lines = ["# News evidence snapshot", ""]
        for index, item in enumerate(result, 1):
            lines.extend([f"## {index}. {item['title'] or '제목 없는 뉴스'}",
                          f"- Date: {item['day'] or 'unknown'}",
                          f"- URL: {item['url'] or 'none'}", "",
                          "### Message excerpt", item["excerpt"] or "없음", "",
                          "### Stored text", item["text"] or "없음", ""])
        return payload, raw, "\n".join(lines), redactions

    def create_run(self, items, request):
        request = dict(request or {})
        title = _clean(_redact(request.get("title"))[0])
        requirement = _clean(_redact(request.get("requirement"))[0])
        if not title or len(title) > 200 or not requirement or len(requirement) > 4000:
            raise ValueError("제목과 분석 요구사항의 길이를 확인해 주세요.")
        try:
            rounds = int(request.get("rounds", 20))
        except (TypeError, ValueError):
            raise ValueError("라운드는 정수여야 합니다.") from None
        if not 1 <= rounds <= MAX_ROUNDS:
            raise ValueError(f"라운드는 1~{MAX_ROUNDS} 범위여야 합니다.")
        platform = str(request.get("platform") or "parallel")
        if platform not in {"twitter", "reddit", "parallel"}:
            raise ValueError("플랫폼은 twitter, reddit, parallel 중 하나여야 합니다.")
        snapshot, raw, markdown, _ = self._snapshot(items)
        run_id = uuid4().hex
        directory = self.artifact_root / run_id
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        json_path, markdown_path = directory / "snapshot.json", directory / "seed.md"
        json_path.write_bytes(raw); markdown_path.write_text(markdown, encoding="utf-8")
        json_path.chmod(0o600); markdown_path.chmod(0o600)
        now = _now()
        with self._connect() as db:
            db.execute("""INSERT INTO mirofish_runs
                (id,status,stage,progress,title,requirement,rounds,platform,item_count,
                 snapshot_bytes,seed_markdown_path,snapshot_json_path,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    run_id, "draft", "draft", 0, title, requirement, rounds, platform,
                    snapshot["item_count"], len(raw), str(markdown_path), str(json_path), now, now))
        return self.get(run_id)

    @staticmethod
    def _public(row):
        result = dict(row)
        for field in ("result", "report"):
            try:
                result[field] = json.loads(result[field]) if result.get(field) else None
            except json.JSONDecodeError:
                result[field] = None
        return result

    def get(self, run_id):
        run_id = self._validate_id(run_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM mirofish_runs WHERE id=?", (run_id,)).fetchone()
        return self._public(row) if row else None

    def list(self, limit=50):
        limit = max(1, min(200, int(limit)))
        with self._connect() as db:
            rows = db.execute("SELECT * FROM mirofish_runs ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        return [self._public(row) for row in rows]

    def get_run(self, run_id):
        return self.get(run_id)

    def list_runs(self, limit=50):
        return self.list(limit)

    def seed(self, run_id):
        run = self.get(run_id)
        if not run:
            raise KeyError("실행을 찾을 수 없습니다.")
        path = Path(run["snapshot_json_path"])
        expected_parent = (self.artifact_root / run_id).resolve()
        if path.resolve().parent != expected_parent or path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise RuntimeError("스냅샷 파일을 안전하게 읽을 수 없습니다.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("스냅샷 파일을 읽을 수 없습니다.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("스냅샷 형식이 올바르지 않습니다.")
        return payload

    def _update(self, run_id, **values):
        allowed = {"status", "stage", "progress", "project_id", "graph_task_id", "graph_id",
                   "prepare_task_id", "simulation_id", "report_id", "report_task_id",
                   "close_requested", "error", "result", "report", "started_at", "completed_at"}
        if not values or set(values) - allowed:
            raise ValueError("저장할 수 없는 실행 필드입니다.")
        for field in ("result", "report"):
            if field in values and not isinstance(values[field], str):
                values[field] = json.dumps(values[field], ensure_ascii=False)
        values["updated_at"] = _now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self._connect() as db:
            db.execute(f"UPDATE mirofish_runs SET {assignments} WHERE id=?",
                       (*values.values(), run_id))

    @staticmethod
    def _data(response, allow_pending=False):
        if not isinstance(response, dict):
            raise RuntimeError("MiroFish 응답 형식이 올바르지 않습니다.")
        if response.get("success") is not True:
            if allow_pending and response.get("pending"):
                return dict(response.get("data") or {})
            raise RuntimeError(_safe_error(response.get("error") or "MiroFish 요청 실패"))
        data = response.get("data")
        return dict(data) if isinstance(data, dict) else {}

    def _request(self, method, path, **kwargs):
        return self._data(self.transport.request(method, path, **kwargs))

    def start(self, run_id):
        run = self.get(run_id)
        if not run:
            raise KeyError("실행을 찾을 수 없습니다.")
        if run["status"] == "completed":
            return run
        if run["status"] in _ACTIVE:
            return run
        ready = self.availability()
        if not ready["available"]:
            raise RuntimeError(ready["reason"])
        with self._lock:
            if self._closed:
                raise RuntimeError("MiroFish 서비스가 종료되었습니다.")
            pending = sum(1 for future in self._jobs.values() if not future.done())
            if pending >= MAX_PENDING_RUNS:
                raise RuntimeError("MiroFish 실행 대기열이 가득 찼습니다.")
            self._update(run_id, status="queued", error=None,
                         started_at=run.get("started_at") or _now())
            self._schedule(run_id)
        return self.get(run_id)

    def resume(self, run_id):
        run = self.get(run_id)
        if not run:
            raise KeyError("실행을 찾을 수 없습니다.")
        if run["status"] not in {"paused", "failed", "stopped"}:
            return run
        if run["status"] in {"failed", "stopped"}:
            if run["stage"] == "graph":
                self._update(run_id, graph_task_id=None)
            elif run["stage"] == "simulation_prepare":
                self._update(run_id, prepare_task_id=None, stage="simulation_create")
            elif run["stage"] == "simulation_run":
                self._update(run_id, prepare_task_id=None, stage="simulation_create",
                             close_requested=0)
            elif run["stage"] == "report":
                self._update(run_id, report_id=None, report_task_id=None)
        return self.start(run_id)

    def _schedule(self, run_id):
        with self._lock:
            future = self._jobs.get(run_id)
            if future and not future.done():
                return
            if self._closed:
                raise RuntimeError("MiroFish 서비스가 종료되었습니다.")
            self._jobs[run_id] = self._executor.submit(self._workflow, run_id)

    def _status(self, data):
        return str(data.get("runner_status") or data.get("status") or "").casefold()

    def _halt_if_closing(self, run_id):
        if not self._closing.is_set():
            return False
        run = self.get(run_id)
        if run and run["status"] not in {"completed", "failed", "stopped"}:
            self._update(run_id, status="paused", error="MiroFish 서비스 종료로 일시 중지됨")
        return True

    def _poll(self, run_id, method, path, payload, terminal, failed, stage, id_field=None):
        for _ in range(self.max_polls):
            if self._halt_if_closing(run_id):
                return None
            local = self.get(run_id)
            if local["status"] == "stop_requested" and stage != "simulation_run":
                self._update(run_id, status="stopped", error=None)
                return None
            data = self._request(method, path, json_body=payload)
            status = self._status(data)
            progress = data.get("progress")
            updates = {"stage": stage, "result": data}
            if isinstance(progress, (int, float)):
                updates["progress"] = max(0, min(99, int(progress)))
            if id_field and data.get(id_field):
                updates[id_field] = str(data[id_field])
            self._update(run_id, **updates)
            if status in terminal:
                return data
            if status in failed:
                raise RuntimeError(data.get("error") or f"MiroFish {stage} 실패")
            if self.poll_interval and self._closing.wait(self.poll_interval):
                self._halt_if_closing(run_id)
                return None
        self._update(run_id, status="paused", error=f"{stage} 상태 확인 시간 초과")
        return None

    @staticmethod
    def _rounds_finished(data, rounds, platform):
        if platform == "parallel":
            if "twitter_completed" in data or "reddit_completed" in data:
                return data.get("twitter_completed") is True and data.get("reddit_completed") is True
            try:
                return (int(data.get("twitter_current_round", -1)) >= int(rounds)
                        and int(data.get("reddit_current_round", -1)) >= int(rounds))
            except (TypeError, ValueError):
                return False
        prefix = "twitter" if platform == "twitter" else "reddit"
        completed_field = f"{prefix}_completed"
        if completed_field in data:
            return data.get(completed_field) is True
        try:
            if int(data.get(f"{prefix}_current_round", data.get("current_round", -1))) >= int(rounds):
                return True
        except (TypeError, ValueError):
            pass
        return False

    def _poll_simulation(self, run_id):
        for _ in range(self.max_polls):
            if self._halt_if_closing(run_id):
                return False
            run = self.get(run_id)
            data = self._request("GET", f"/api/simulation/{run['simulation_id']}/run-status")
            status = self._status(data)
            progress = data.get("progress_percent", data.get("progress"))
            updates = {"stage": "simulation_run", "result": data}
            if isinstance(progress, (int, float)):
                updates["progress"] = max(65, min(84, 65 + int(float(progress) * .19)))
            self._update(run_id, **updates)
            run = self.get(run_id)
            if status == "failed":
                raise RuntimeError(data.get("error") or "MiroFish simulation_run 실패")
            if run["status"] == "stop_requested":
                if status in {"stopped", "completed"}:
                    self._update(run_id, status="stopped", error=None, completed_at=_now())
                    return False
            else:
                if status == "completed":
                    return True
                if status == "stopped" and run["close_requested"]:
                    return True
                if (not run["close_requested"] and status in {"running", "completed"}
                        and self._rounds_finished(data, run["rounds"], run["platform"])):
                    self._request("POST", "/api/simulation/close-env", json_body={
                        "simulation_id": run["simulation_id"], "timeout": 30})
                    self._update(run_id, close_requested=1, result=data)
            if self.poll_interval and self._closing.wait(self.poll_interval):
                self._halt_if_closing(run_id)
                return False
        self._update(run_id, status="paused", error="simulation_run 상태 확인 시간 초과")
        return False

    def _finish_stop(self, run_id):
        run = self.get(run_id)
        if not run or not run["simulation_id"]:
            self._update(run_id, status="stopped", error=None, completed_at=_now())
            return
        self._poll_simulation(run_id)

    def _workflow(self, run_id):
        try:
            if self._halt_if_closing(run_id):
                return
            initial = self.get(run_id)
            if initial["status"] == "stop_requested":
                self._finish_stop(run_id)
                return
            self._update(run_id, status="running", error=None)
            run = self.get(run_id)
            if not run["project_id"]:
                seed = Path(run["seed_markdown_path"]).read_bytes()
                data = self._request("POST", "/api/graph/ontology/generate",
                    fields={"simulation_requirement": run["requirement"],
                            "project_name": run["title"],
                            "additional_context": "뉴스 스냅샷 근거만 사용"},
                    files={"files": ("news-seed.md", seed, "text/markdown")})
                project_id = self._upstream_id(data.get("project_id"), "project_id")
                self._update(run_id, stage="ontology", progress=10, project_id=project_id,
                             result=data)
            if self._halt_if_closing(run_id):
                return
            run = self.get(run_id)
            if not run["graph_id"]:
                if not run["graph_task_id"]:
                    data = self._request("POST", "/api/graph/build",
                                         json_body={"project_id": run["project_id"],
                                                    "graph_name": run["title"]})
                    task_id = self._upstream_id(data.get("task_id"), "graph task_id")
                    self._update(run_id, stage="graph", progress=15, graph_task_id=task_id,
                                 result=data)
                run = self.get(run_id)
                try:
                    data = self._poll(run_id, "GET", f"/api/graph/task/{run['graph_task_id']}",
                                      None, {"completed"}, {"failed"}, "graph")
                except RuntimeError as error:
                    if "HTTP 404" not in str(error):
                        raise
                    project = self._request("GET", f"/api/graph/project/{run['project_id']}")
                    graph_id = project.get("graph_id") or (project.get("project") or {}).get("graph_id")
                    if not graph_id:
                        raise RuntimeError("그래프 작업 상태를 복구할 수 없습니다.") from None
                    graph_id = self._upstream_id(graph_id, "graph_id")
                    self._update(run_id, graph_id=graph_id, progress=35, result=project)
                    data = None
                if data is None:
                    if not self.get(run_id)["graph_id"]:
                        return
                else:
                    graph_id = data.get("graph_id") or (data.get("result") or {}).get("graph_id")
                    if not graph_id:
                        raise RuntimeError("완료된 그래프 작업에 graph_id가 없습니다.")
                    graph_id = self._upstream_id(graph_id, "graph_id")
                    self._update(run_id, graph_id=graph_id, progress=35)
            if self._halt_if_closing(run_id):
                return
            run = self.get(run_id)
            if not run["simulation_id"]:
                data = self._request("POST", "/api/simulation/create", json_body={
                    "project_id": run["project_id"], "graph_id": run["graph_id"],
                    "enable_twitter": True, "enable_reddit": True,
                })
                simulation_id = self._upstream_id(data.get("simulation_id"), "simulation_id")
                self._update(run_id, stage="simulation_create", progress=40,
                             simulation_id=simulation_id, result=data)
            if self._halt_if_closing(run_id):
                return
            run = self.get(run_id)
            if run["stage"] not in {"simulation_run", "report", "completed"}:
                if not run["prepare_task_id"]:
                    data = self._request("POST", "/api/simulation/prepare", json_body={
                        "simulation_id": run["simulation_id"], "use_llm_for_profiles": True})
                    raw_task_id = data.get("task_id")
                    task_id = self._upstream_id(raw_task_id, "prepare task_id") if raw_task_id else ""
                    status = self._status(data)
                    self._update(run_id, stage="simulation_prepare", progress=45,
                                 prepare_task_id=task_id or None, result=data)
                    if status not in {"ready", "completed", "already_prepared"} and not task_id:
                        raise RuntimeError("MiroFish가 prepare task_id를 반환하지 않았습니다.")
                run = self.get(run_id)
                if run["prepare_task_id"]:
                    data = self._poll(run_id, "POST", "/api/simulation/prepare/status",
                                      {"task_id": run["prepare_task_id"],
                                       "simulation_id": run["simulation_id"]},
                                      {"ready", "completed"}, {"failed"}, "simulation_prepare")
                    if data is None:
                        return
                self._update(run_id, progress=60)
            if self._halt_if_closing(run_id):
                return
            run = self.get(run_id)
            if run["stage"] not in {"simulation_run", "report", "completed"}:
                data = self._request("POST", "/api/simulation/start", json_body={
                    "simulation_id": run["simulation_id"], "platform": run["platform"],
                    "max_rounds": run["rounds"], "enable_graph_memory_update": True})
                self._update(run_id, stage="simulation_run", progress=65, result=data)
            run = self.get(run_id)
            if run["stage"] == "simulation_run":
                if not self._poll_simulation(run_id):
                    return
                self._update(run_id, stage="report", progress=85)
            if self._halt_if_closing(run_id):
                return
            run = self.get(run_id)
            if not run["report_id"]:
                data = self._request("POST", "/api/report/generate",
                                     json_body={"simulation_id": run["simulation_id"]})
                report_id = self._upstream_id(data.get("report_id"), "report_id")
                raw_task_id = data.get("task_id")
                task_id = self._upstream_id(raw_task_id, "report task_id") if raw_task_id else ""
                self._update(run_id, stage="report", progress=90, report_id=report_id,
                             report_task_id=task_id or None, result=data)
            run = self.get(run_id)
            status = self._status(run.get("result") or {})
            if status != "completed":
                data = self._poll(run_id, "POST", "/api/report/generate/status",
                                  {"task_id": run["report_task_id"],
                                   "simulation_id": run["simulation_id"]},
                                  {"completed"}, {"failed"}, "report")
                if data is None:
                    return
            report = self._request("GET", f"/api/report/{run['report_id']}")
            self._update(run_id, status="completed", stage="completed", progress=100,
                         report=report, result=report, error=None, completed_at=_now())
        except Exception as error:
            current = self.get(run_id)
            if current and current["status"] != "stopped":
                self._update(run_id, status="failed", error=_safe_error(error))

    def stop(self, run_id):
        run = self.get(run_id)
        if not run:
            raise KeyError("실행을 찾을 수 없습니다.")
        if run["status"] in {"completed", "failed", "stopped"}:
            return run
        if not run["simulation_id"]:
            self._update(run_id, status="stopped", error=None, completed_at=_now())
            return self.get(run_id)
        self._update(run_id, status="stop_requested")
        try:
            response = self.transport.request("POST", "/api/simulation/stop",
                                              json_body={"simulation_id": run["simulation_id"]})
            self._data(response, allow_pending=True)
        except Exception as error:
            self._update(run_id, status="paused", error=_safe_error(error))
            return self.get(run_id)
        self._schedule(run_id)
        return self.get(run_id)

    def report(self, run_id):
        run = self.get(run_id)
        if not run:
            raise KeyError("실행을 찾을 수 없습니다.")
        return {"id": run_id, "status": run["status"], "report_id": run["report_id"],
                "report": run["report"]}

    def interview(self, run_id, agent_id, prompt, platform=None):
        run = self.get(run_id)
        if not run or not run["simulation_id"]:
            raise KeyError("실행 또는 시뮬레이션을 찾을 수 없습니다.")
        try:
            agent_id = int(agent_id)
        except (TypeError, ValueError):
            raise ValueError("agent_id는 0 이상의 정수여야 합니다.") from None
        prompt = _clean(_redact(prompt)[0])
        if agent_id < 0 or not prompt or len(prompt) > 4000:
            raise ValueError("agent_id 또는 질문 길이를 확인해 주세요.")
        body = {"simulation_id": run["simulation_id"], "agent_id": agent_id, "prompt": prompt}
        if platform:
            if platform not in {"twitter", "reddit"}:
                raise ValueError("인터뷰 플랫폼을 확인해 주세요.")
            body["platform"] = platform
        return self._request("POST", "/api/simulation/interview", json_body=body)

    def report_chat(self, run_id, message, chat_history=None):
        run = self.get(run_id)
        if not run or not run["simulation_id"]:
            raise KeyError("실행 또는 시뮬레이션을 찾을 수 없습니다.")
        message = _clean(_redact(message)[0])
        history = list(chat_history or [])
        if not message or len(message) > 4000 or len(history) > 100:
            raise ValueError("메시지 또는 대화 기록 길이를 확인해 주세요.")
        clean_history = []
        for item in history:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                raise ValueError("대화 기록 형식이 올바르지 않습니다.")
            content = _clean(_redact(item.get("content"))[0])
            if len(content) > 4000:
                raise ValueError("대화 기록이 너무 깁니다.")
            clean_history.append({"role": item["role"], "content": content})
        return self._request("POST", "/api/report/chat", json_body={
            "simulation_id": run["simulation_id"], "message": message,
            "chat_history": clean_history})

    def close(self):
        with self._lock:
            self._closed = True
            self._closing.set()
            with self._connect() as db:
                db.execute("""UPDATE mirofish_runs SET status='paused',
                    error='MiroFish 서비스 종료로 일시 중지됨',updated_at=?
                    WHERE status IN ('queued','running','stop_requested')""", (_now(),))
        self._executor.shutdown(wait=False, cancel_futures=True)
