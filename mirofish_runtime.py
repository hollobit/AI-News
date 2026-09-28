"""Install and supervise the isolated, vendored MiroFish runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import signal
import socket
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
INTEGRATION = ROOT / "integrations" / "mirofish"
UPSTREAM = INTEGRATION / "upstream"
RUNTIME = ROOT / ".runtime" / "mirofish"
WORKTREE = RUNTIME / "worktree"
VENV = RUNTIME / "venv"
FRONTEND_SOURCE = RUNTIME / "frontend-src"
FRONTEND_DIST = FRONTEND_SOURCE / "dist"
UPLOADS = RUNTIME / "uploads"
CONFIG_PATH = ROOT / ".env.mirofish"
INSTALL_MARKER = RUNTIME / "install.json"
COMMIT = "39d849138ef254f6c737ab4c4705e5545dbe31d4"
SETTINGS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME", "ZEP_API_KEY")
SAFE_PARENT_ENV = ("SSL_CERT_FILE", "SSL_CERT_DIR")
SERVICES = {
    "backend": {"port": 5001, "url": "http://127.0.0.1:5001", "marker": "run.py"},
    "frontend": {"port": 3000, "url": "http://127.0.0.1:3000", "marker": "_serve-frontend"},
}


def _ensure_runtime_dirs() -> None:
    for path in (RUNTIME, RUNTIME / "logs", RUNTIME / "pids", RUNTIME / "tmp",
                 RUNTIME / "uv-cache", RUNTIME / "npm-cache", RUNTIME / "cache", UPLOADS):
        path.mkdir(parents=True, exist_ok=True)


def ensure_config() -> None:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text("".join(f"{key}=\n" for key in SETTINGS), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)


def load_settings(path: Path = CONFIG_PATH) -> dict[str, str]:
    """Load supported literal values without executing or expanding the file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        key = key.strip()
        if separator and key in SETTINGS:
            values[key] = value.strip()
    return values


def _clean_environment(settings: dict[str, str] | None = None) -> dict[str, str]:
    """Construct a child environment that cannot inherit Telegram credentials."""
    _ensure_runtime_dirs()
    environment = {
        "PATH": os.pathsep.join((str(VENV / "bin"), "/opt/homebrew/bin", "/usr/local/bin",
                                 "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
        "TMPDIR": str(RUNTIME / "tmp"),
        "XDG_CACHE_HOME": str(RUNTIME / "cache"),
        "HF_HOME": str(RUNTIME / "cache" / "huggingface"),
        "TORCH_HOME": str(RUNTIME / "cache" / "torch"),
        "MPLCONFIGDIR": str(RUNTIME / "cache" / "matplotlib"),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "FLASK_HOST": "127.0.0.1",
        "FLASK_PORT": "5001",
        "FLASK_DEBUG": "False",
        "UV_CACHE_DIR": str(RUNTIME / "fresh-cache"),
        "npm_config_cache": str(RUNTIME / "npm-cache"),
    }
    for key in SAFE_PARENT_ENV:
        if os.environ.get(key):
            environment[key] = os.environ[key]
    for key, value in (settings or {}).items():
        if key in SETTINGS and value:
            environment[key] = value
    return environment


def _run(command: list[str], *, cwd: Path | None = None,
         settings: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=_clean_environment(settings), check=True)


def _copy_runtime_sources() -> None:
    if not (UPSTREAM / "backend" / "uv.lock").is_file() or not (
            UPSTREAM / "frontend" / "package-lock.json").is_file():
        raise RuntimeError("vendored MiroFish source is incomplete")
    old_uploads = WORKTREE / "backend" / "uploads"
    _preserve_existing_uploads(old_uploads)
    staging = RUNTIME / "worktree.next"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(UPSTREAM / "backend", staging / "backend",
                    ignore=shutil.ignore_patterns(".venv", "uploads", "__pycache__"))
    (staging / "backend" / "uploads").symlink_to(UPLOADS, target_is_directory=True)
    shutil.copytree(UPSTREAM / "locales", staging / "locales")
    (staging / ".env").write_text("".join(f"{key}=\n" for key in SETTINGS),
                                  encoding="utf-8")
    os.chmod(staging / ".env", 0o600)

    app_factory = staging / "backend" / "app" / "__init__.py"
    factory_text = app_factory.read_text(encoding="utf-8")
    old_cors = 'CORS(app, resources={r"/api/*": {"origins": "*"}})'
    new_cors = (
        'CORS(app, resources={r"/api/*": {"origins": ['
        '"http://127.0.0.1:3000", "http://localhost:3000", '
        '"http://127.0.0.1:8000", "http://localhost:8000"]}})'
    )
    if factory_text.count(old_cors) != 1:
        raise RuntimeError("upstream backend CORS patch no longer applies cleanly")
    app_factory.write_text(factory_text.replace(old_cors, new_cors), encoding="utf-8")
    guard_anchor = new_cors + "\n"
    request_guard = r'''

    allowed_hosts = {"127.0.0.1", "localhost", "127.0.0.1:5001", "localhost:5001"}
    allowed_origins = {
        "http://127.0.0.1:3000", "http://localhost:3000",
        "http://127.0.0.1:8000", "http://localhost:8000",
    }

    @app.before_request
    def enforce_local_request_boundary():
        if request.host not in allowed_hosts:
            return {"success": False, "error": "invalid host"}, 403
        origin = request.headers.get("Origin")
        if origin and origin not in allowed_origins:
            return {"success": False, "error": "origin not allowed"}, 403

    @app.after_request
    def remove_internal_error_details(response):
        if response.status_code >= 400 and response.is_json:
            payload = response.get_json(silent=True)
            if isinstance(payload, dict) and "traceback" in payload:
                payload.pop("traceback", None)
                response.set_data(app.json.dumps(payload))
        return response
'''
    factory_text = app_factory.read_text(encoding="utf-8")
    if factory_text.count(guard_anchor) != 1:
        raise RuntimeError("runtime request guard patch no longer applies cleanly")
    app_factory.write_text(factory_text.replace(guard_anchor, guard_anchor + request_guard),
                           encoding="utf-8")
    if WORKTREE.exists():
        shutil.rmtree(WORKTREE)
    staging.replace(WORKTREE)

    if FRONTEND_SOURCE.exists():
        shutil.rmtree(FRONTEND_SOURCE)
    shutil.copytree(UPSTREAM / "frontend", FRONTEND_SOURCE,
                    ignore=shutil.ignore_patterns("node_modules", "dist"))
    shutil.copytree(UPSTREAM / "locales", RUNTIME / "locales", dirs_exist_ok=True)
    report_api = FRONTEND_SOURCE / "src" / "api" / "report.js"
    report_text = report_api.read_text(encoding="utf-8")
    old_status = "return service.get(`/api/report/generate/status`, { params: { report_id: reportId } })"
    new_status = "return service.post('/api/report/generate/status', { task_id: reportId })"
    if report_text.count(old_status) != 1:
        raise RuntimeError("upstream frontend report-status patch no longer applies cleanly")
    report_api.write_text(report_text.replace(old_status, new_status), encoding="utf-8")
    package_path = FRONTEND_SOURCE / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package.setdefault("overrides", {}).update({"nanoid": "3.3.18", "postcss": "8.5.28"})
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


def _python_candidate() -> str:
    for candidate in sorted((ROOT / '.runtime' / 'python').glob('cpython-3.12*/bin/python3.12')):
        if candidate.is_file():
            return str(candidate)
    for name in ("python3.12", "python3.11"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    raise RuntimeError("Python 3.11 or 3.12 is required")


def _preserve_existing_uploads(old_uploads: Path) -> None:
    """Migrate a legacy worktree upload directory without overwriting state."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if old_uploads.is_symlink() or not old_uploads.exists():
        return
    if not old_uploads.is_dir():
        raise RuntimeError("legacy MiroFish uploads path is not a directory")
    for child in old_uploads.iterdir():
        target = UPLOADS / child.name
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"cannot migrate existing upload state: {child.name}")
        child.replace(target)
    old_uploads.rmdir()


def install() -> dict:
    """Build an exact-lock Python environment and production frontend."""
    _ensure_runtime_dirs()
    ensure_config()
    if any(_service_running(name)[0] for name in SERVICES) or _owned_simulation_processes():
        raise RuntimeError("stop MiroFish before reinstalling")
    _copy_runtime_sources()
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if not uv or not npm:
        raise RuntimeError("uv and npm are required")
    python = _python_candidate()
    _run([uv, "venv", str(VENV), "--python", python, "--clear", "--no-python-downloads"])
    requirements = RUNTIME / "requirements.lock"
    _run([uv, "export", "--project", str(WORKTREE / "backend"), "--locked",
          "--no-emit-project", "--output-file", str(requirements)])
    _run([uv, "pip", "sync", "--python", str(VENV / "bin" / "python"),
          str(requirements), "--cache-dir", str(RUNTIME / "fresh-cache")])
    _run([npm, "install", "--package-lock-only", "--ignore-scripts"], cwd=FRONTEND_SOURCE)
    _run([npm, "ci", "--ignore-scripts"], cwd=FRONTEND_SOURCE)
    _run([npm, "run", "build"], cwd=FRONTEND_SOURCE)

    smoke = subprocess.run(
        [str(VENV / "bin" / "python"), "-c",
         "import importlib.metadata as m; import oasis; print(m.version('camel-oasis'))"],
        cwd=WORKTREE / "backend", env=_clean_environment(), capture_output=True,
        text=True, timeout=60, check=True,
    )
    oasis_version = smoke.stdout.strip().splitlines()[-1]
    subprocess.run(
        [str(VENV / "bin" / "python"), "scripts/run_parallel_simulation.py", "--help"],
        cwd=WORKTREE / "backend", env=_clean_environment(), capture_output=True,
        text=True, timeout=60, check=True,
    )
    subprocess.run(
        [str(VENV / "bin" / "python"), "-m", "pytest", "-q",
         "tests/test_platform_profiles.py", "tests/test_profile_field_normalization.py",
         "tests/test_simulation_prepare_failure.py"],
        cwd=WORKTREE / "backend", env=_clean_environment(), capture_output=True,
        text=True, timeout=120, check=True,
    )
    marker = {
        "commit": COMMIT,
        "python": subprocess.check_output(
            [str(VENV / "bin" / "python"), "--version"], text=True,
            env=_clean_environment()).strip(),
        "oasis_version": oasis_version,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    INSTALL_MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return marker


def _runtime_env_file(settings: dict[str, str]) -> None:
    target = WORKTREE / ".env"
    target.write_text("".join(f"{key}={settings.get(key, '')}\n" for key in SETTINGS),
                      encoding="utf-8")
    os.chmod(target, 0o600)


def _pid_path(service: str) -> Path:
    return RUNTIME / "pids" / f"{service}.pid"


def _read_pid(service: str) -> int | None:
    try:
        value = int(_pid_path(service).read_text(encoding="ascii").strip())
        return value if value > 1 else None
    except (OSError, ValueError):
        return None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _owned_process(service: str, pid: int) -> bool:
    if not _process_exists(pid):
        return False
    try:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True, timeout=2,
                                 check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return SERVICES[service]["marker"] in command and str(ROOT) in command


def _service_running(service: str) -> tuple[bool, int | None]:
    pid = _read_pid(service)
    return (bool(pid and _owned_process(service, pid)), pid)


def _owned_simulation_processes() -> list[dict[str, int]]:
    """Find only OASIS runners whose executable, script, and config are ours."""
    try:
        output = subprocess.run(["ps", "-axo", "pid=,pgid=,command="], capture_output=True,
                                text=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    script_root = str(WORKTREE / "backend" / "scripts") + os.sep
    venv_root = str(VENV) + os.sep
    upload_root = UPLOADS.resolve()
    found = []
    for line in output.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            pid, pgid = int(fields[0]), int(fields[1])
            command = shlex.split(fields[2])
        except (ValueError, OSError):
            continue
        if not command or not command[0].startswith(venv_root):
            continue
        scripts = [arg for arg in command if arg.startswith(script_root) and
                   Path(arg).name in {"run_parallel_simulation.py", "run_twitter_simulation.py",
                                      "run_reddit_simulation.py"}]
        if len(scripts) != 1 or "--config" not in command:
            continue
        try:
            config = Path(command[command.index("--config") + 1]).resolve()
            config.relative_to(upload_root)
        except (IndexError, ValueError, OSError):
            continue
        found.append({"pid": pid, "pgid": pgid})
    return found


def _port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_ready(service: str, process: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{service} exited during startup; inspect its log")
        if _port_ready(SERVICES[service]["port"]):
            return
        time.sleep(0.1)
    raise RuntimeError(f"{service} did not become ready")


def _spawn(service: str, command: list[str], cwd: Path,
           environment: dict[str, str]) -> subprocess.Popen:
    log_path = RUNTIME / "logs" / f"{service}.log"
    log = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True, text=True)
    finally:
        log.close()
    _pid_path(service).write_text(f"{process.pid}\n", encoding="ascii")
    return process


def start() -> dict:
    state = runtime_status()
    if not state["installed"]:
        raise RuntimeError("MiroFish is not installed; run install first")
    settings = load_settings()
    missing = [key for key in SETTINGS if not settings.get(key)]
    if missing:
        raise RuntimeError("MiroFish configuration is incomplete: " + ", ".join(missing))
    _runtime_env_file(settings)
    environment = _clean_environment(settings)
    started: list[tuple[str, subprocess.Popen]] = []
    try:
        backend_running, _ = _service_running("backend")
        if not backend_running:
            if _port_ready(5001):
                raise RuntimeError("port 5001 is already in use")
            process = _spawn("backend", [str(VENV / "bin" / "python"), "run.py"],
                             WORKTREE / "backend", environment)
            started.append(("backend", process))
            _wait_ready("backend", process)

        frontend_running, _ = _service_running("frontend")
        if not frontend_running:
            if _port_ready(3000):
                raise RuntimeError("port 3000 is already in use")
            process = _spawn("frontend", [sys.executable, str(Path(__file__).resolve()),
                                           "_serve-frontend"], ROOT, environment)
            started.append(("frontend", process))
            _wait_ready("frontend", process)
    except Exception:
        for service, process in reversed(started):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
            _pid_path(service).unlink(missing_ok=True)
        raise
    return runtime_status()


def stop() -> dict:
    _request_simulation_stops()
    _terminate_owned_simulations()
    for service in SERVICES:
        pid = _read_pid(service)
        if not pid:
            continue
        if _owned_process(service, pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 8
            while _process_exists(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if _process_exists(pid) and _owned_process(service, pid):
                os.killpg(pid, signal.SIGKILL)
        _pid_path(service).unlink(missing_ok=True)
    return runtime_status()


def _request_simulation_stops() -> None:
    """Ask the live backend to flush and stop simulations before termination."""
    running, _ = _service_running("backend")
    if not running:
        return
    try:
        with urlopen(Request("http://127.0.0.1:5001/api/simulation/list",
                             headers={"Host": "127.0.0.1:5001"}), timeout=3) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
    except (OSError, HTTPError, URLError, ValueError, TypeError):
        return
    active = {"preparing", "ready", "starting", "running", "paused", "stopping"}
    for simulation in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(simulation, dict) or simulation.get("status") not in active:
            continue
        simulation_id = simulation.get("simulation_id")
        if not isinstance(simulation_id, str) or not simulation_id.startswith("sim_"):
            continue
        body = json.dumps({"simulation_id": simulation_id}).encode("utf-8")
        request = Request("http://127.0.0.1:5001/api/simulation/stop", data=body,
                          headers={"Host": "127.0.0.1:5001",
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10):
                pass
        except (OSError, HTTPError, URLError):
            pass


def _terminate_owned_simulations() -> None:
    processes = _owned_simulation_processes()
    groups = {item["pgid"] for item in processes if item["pgid"] > 1}
    for pgid in groups:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 8
    while _owned_simulation_processes() and time.monotonic() < deadline:
        time.sleep(0.1)
    for item in _owned_simulation_processes():
        if item["pgid"] > 1:
            try:
                os.killpg(item["pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass


def runtime_status() -> dict:
    settings = load_settings()
    missing = [key for key in SETTINGS if not settings.get(key)]
    marker = {}
    try:
        marker = json.loads(INSTALL_MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    installed = (marker.get("commit") == COMMIT and (VENV / "bin" / "python").is_file()
                 and FRONTEND_DIST.joinpath("index.html").is_file())
    services = {}
    for service, info in SERVICES.items():
        running, pid = _service_running(service)
        services[service] = {"running": running, "pid": pid if running else None,
                             "url": info["url"]}
    return {
        "installed": installed,
        "configured": not missing,
        "missing_settings": missing,
        "commit": COMMIT,
        "oasis_available": bool(installed and marker.get("oasis_version")),
        "oasis_version": marker.get("oasis_version", ""),
        "frontend_built": FRONTEND_DIST.joinpath("index.html").is_file(),
        "active_simulation_processes": len(_owned_simulation_processes()),
        "backend": services["backend"],
        "frontend": services["frontend"],
        "logs": {name: str(RUNTIME / "logs" / f"{name}.log") for name in SERVICES},
    }


def source_files() -> tuple[Path, ...]:
    """Return trusted regular files in the immutable vendored source bundle."""
    base = UPSTREAM.resolve()
    excluded = {".git", "node_modules", ".venv", "uploads", ".env", "__pycache__"}
    files = []
    for candidate in sorted(base.rglob("*")):
        if any(part in excluded for part in candidate.relative_to(base).parts):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            continue
        files.append(resolved)
    return tuple(files)


def start_runtime() -> dict:
    """Import-friendly lifecycle entry point used by the local news server."""
    return start()


def stop_runtime() -> dict:
    """Import-friendly lifecycle entry point used by the local news server."""
    return stop()


class _SPAHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIST), **kwargs)

    def do_GET(self):
        requested = Path(self.translate_path(self.path))
        if not requested.exists() and not self.path.startswith("/assets/"):
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format, *args):
        return


def _serve_frontend() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 3000), _SPAHandler)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "start", "status", "stop",
                                             "_serve-frontend"))
    args = parser.parse_args(argv)
    if args.command == "_serve-frontend":
        _serve_frontend()
        return 0
    try:
        if args.command == "install":
            result = install()
        elif args.command == "start":
            result = start()
        elif args.command == "stop":
            result = stop()
        else:
            result = runtime_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"MiroFish {args.command} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
