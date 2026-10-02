"""Content-free pipeline incidents, acknowledgement and local notifications."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time

from operations_health import age, alive
from model_policy import MODELS

ROOT = Path(__file__).resolve().parent
STORE = ROOT / '.runtime/operations-incidents.sqlite3'


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def snapshot(path, runtime=None, stamp=None):
    stamp = time.time() if stamp is None else stamp
    runtime = Path(runtime or ROOT / '.runtime')
    result = {'checked_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
              'stages': [], 'problems': [], 'models': MODELS}

    def problem(code, message, action):
        result['problems'].append(dict(code=code, message=message, action=action))

    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        states = dict(db.execute("SELECT key,value FROM state WHERE key IN ('collector_last_success','collector_corpus_snapshot')"))
        checked = states.get('collector_last_success')
        result['stages'].append(dict(name='Telegram 확인', status='정상' if age(checked, stamp) < 300 else '지연', updated_at=checked, model='API · 모델 호출 없음'))
        if age(checked, stamp) > 300:
            problem('collector_stale', 'Telegram 확인이 5분 이상 갱신되지 않았습니다.', '수집기 실행과 네트워크 연결을 확인하세요.')
        try:
            extraction = json.loads(states.get('collector_corpus_snapshot', '{}'))
        except ValueError:
            extraction = {}
        result['stages'].append(dict(name='기사 추출·중복 제거', status=extraction.get('status', 'unknown'), updated_at=extraction.get('extracted_at'), count=extraction.get('total_unique'), model='규칙 기반 · 모델 호출 없음'))
        if extraction.get('status') != 'complete' and age(extraction.get('checked_at'), stamp) > 600:
            problem('extraction', '기사 추출이 완료되지 않았습니다.', '수집 오류와 추출 상태를 확인하세요. 새 메시지가 없는 동안 과거 추출 시각은 정상입니다.')
        for kind, table, name in [('baseline', 'bulk_baseline_runs', '기본 분석·독립 검토'), ('deep', 'rsi_cycles', '상세·위험 분석·검토')]:
            row = db.execute(f'SELECT id,status,owner_pid,updated_at FROM {table} ORDER BY created_at DESC LIMIT 1').fetchone()
            if not row:
                problem(kind + '_missing', name + ' 실행 기록이 없습니다.', '운영 화면에서 분석을 시작하세요.')
                continue
            stage = dict(row)
            stage.update(name=name, owner_alive=alive(row['owner_pid']), model='Luna 분석 → Sol 검토' if kind == 'baseline' else 'Sol 분석·검토 → 고위험/반복 보완 Astra')
            if kind == 'deep':
                if not stage['owner_alive'] and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_schedule'").fetchone():
                    launch = db.execute('SELECT launched_pid FROM risk_schedule WHERE enabled=1 AND cycle_id=?', (row['id'],)).fetchone()
                    if launch and alive(launch[0]):
                        stage.update(owner_alive=True, execution_pid=launch[0], starting=True)
                stage['counts'] = dict(db.execute('SELECT status,count(*) FROM corpus_completion_documents WHERE cycle_id=? GROUP BY status', (row['id'],)))
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='completion_engine_waits'").fetchone():
                    waiting = db.execute("""SELECT COUNT(*),MIN(w.next_attempt_at) FROM completion_engine_waits w
                        JOIN rsi_rounds r ON r.id=w.round_id WHERE r.cycle_id=? AND r.status IN ('planned','running')""", (row['id'],)).fetchone()
                    stage['engine_retry_rounds'] = waiting[0]
                    if waiting[0]:
                        stage['engine_retry_at'] = datetime.fromtimestamp(waiting[1], timezone.utc).isoformat()
                        problem('deep_engine_retry', f'상세 분석 {waiting[0]}회차가 일시 오류 후 재시도 대상입니다.',
                                '대기 시간이 지나면 기존 단계에서 재시도합니다. 정상 기사는 계속 처리하며 연속 장애 시 전체 복구 대기로 전환합니다.')
                progress = db.execute('SELECT max(created_at) FROM strategic_workflow_events WHERE run_id IN (SELECT workflow_run_id FROM rsi_rounds WHERE cycle_id=? AND number=(SELECT max(number) FROM rsi_rounds WHERE cycle_id=?))', (row['id'], row['id'])).fetchone()[0]
            else:
                stage['counts'] = dict(db.execute('SELECT status,count(*) FROM bulk_baseline_documents WHERE run_id=? GROUP BY status', (row['id'],)))
                progress = db.execute('SELECT max(created_at) FROM bulk_baseline_events WHERE run_id=?', (row['id'],)).fetchone()[0]
            review_count = sum(stage['counts'].get(key, 0) for key in ('needs_review', 'failed'))
            if review_count:
                problem(kind + '_documents', f'{name}에서 실패·검토 보류 {review_count}건이 남아 있습니다.', '개별 검토 사유를 확인하세요. 다른 문서의 진행과 별개이며, 검토 기준이나 시도 횟수를 초기화하지 않습니다.')
            from engine_errors import infrastructure_error, MESSAGES
            if kind == 'deep':
                failure = db.execute('SELECT error FROM rsi_cycles WHERE id=?', (row['id'],)).fetchone()[0]
            else:
                failure_row = db.execute("SELECT detail FROM bulk_baseline_events WHERE run_id=? AND stage='engine_paused' ORDER BY created_at DESC LIMIT 1", (row['id'],)).fetchone()
                failure = failure_row[0] if failure_row else None
            stage['error_code'] = infrastructure_error(failure) if row['status'] in ('paused', 'error', 'failed') else None
            stage['last_progress_at'] = progress
            result['stages'].append(stage)
            if row['status'] in ('paused', 'error', 'failed', 'requires_review', 'needs_review'):
                problem(kind + '_attention', name + '이 중단 또는 검토 대기 상태입니다.', MESSAGES.get(stage['error_code'], '아래 실행 상세에서 중지 사유와 검토 내용을 확인하세요. 사용자 중지와 검토 거절은 자동 해제하지 않습니다.'))
            elif row['status'] in ('running', 'preparing', 'finishing'):
                if not stage['owner_alive']:
                    problem(kind + '_orphan', name + '의 실행 프로세스가 없습니다.', '스케줄러 복구 기록을 확인하세요. 실행 이력을 지우지 말고 동일 실행을 재개하세요.')
                elif min(age(progress, stamp), age(row['updated_at'], stamp)) > 1800:
                    problem(kind + '_stalled', name + '에서 30분 이상 진행 신호가 없습니다.', '현재 모델 호출·작업자 상태와 제한 대기를 확인하세요.')
        for name in ('collection', 'risks'):
            scheduled = read_json(runtime / ('scheduled-' + name + '.json'))
            result.setdefault('schedulers', {})[name] = {k: scheduled.get(k) for k in ('stage', 'checked_at', 'pending_current_inputs', 'error_code', 'error_type')}
            if age(scheduled.get('checked_at'), stamp) > 900:
                problem(name + '_scheduler', name + ' 정기 실행 기록이 15분 이상 갱신되지 않았습니다.', 'macOS 로그인 상태와 LaunchAgent 등록·오류 로그를 확인하세요.')
            elif scheduled.get('stage') in ('model_access_blocked', 'model_access_required', 'engine_unavailable', 'recovery_exhausted', 'recovery_limit', 'attention_required', 'review_required', 'disabled', 'baseline_attention_required', 'engine_probe_failed', 'dispatch_error'):
                label = '기본 분석' if name == 'collection' else '상세 분석'
                if scheduled.get('stage') == 'dispatch_error':
                    problem(name + '_blocked', label + ' 예약 실행 요청이 실패했습니다.', '웹 서버 응답과 스케줄러 실행 로그를 확인하세요. 다음 정기 점검에서 다시 확인합니다.')
                else:
                    problem(name + '_blocked', label + ' 스케줄러가 중지 또는 검토·복구 대기 중입니다.', '운영 화면의 상태 코드, 모델 접근 점검과 복구 예산을 확인하세요. 사용자 중지나 모델 제한을 우회하지 않습니다.')
    return result


def connect(store):
    Path(store).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(store, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS incidents (
      id INTEGER PRIMARY KEY, code TEXT, message TEXT, action TEXT,
      opened_at TEXT, last_seen_at TEXT, acknowledged_at TEXT, resolved_at TEXT,
      notification_status TEXT, notification_attempt REAL DEFAULT 0);
      CREATE UNIQUE INDEX IF NOT EXISTS incident_open ON incidents(code) WHERE resolved_at IS NULL;
      CREATE TABLE IF NOT EXISTS health_state(id INTEGER PRIMARY KEY, payload TEXT);''')
    return db


def notify(message):
    # Arguments are data, never interpolated into AppleScript source.
    script = 'on run argv\ndisplay notification (item 1 of argv) with title "뉴스 수집·분석 점검" subtitle "운영 화면에서 원인 확인"\nend run'
    try:
        subprocess.run(['osascript', '-e', script, message], check=True, capture_output=True, timeout=15)
        return 'requested'
    except (OSError, subprocess.SubprocessError):
        return 'failed'


def record(report, store=STORE, notifier=notify, stamp=None):
    stamp = time.time() if stamp is None else stamp
    now = report['checked_at']
    with closing(connect(store)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        active = {p['code'] for p in report['problems']}
        for row in db.execute('SELECT id,code FROM incidents WHERE resolved_at IS NULL').fetchall():
            if row['code'] not in active and 'monitor_error' not in active:
                db.execute('UPDATE incidents SET resolved_at=? WHERE id=?', (now, row['id']))
        for p in report['problems']:
            db.execute('INSERT OR IGNORE INTO incidents(code,message,action,opened_at,last_seen_at) VALUES(?,?,?,?,?)', (p['code'], p['message'], p['action'], now, now))
            db.execute('UPDATE incidents SET last_seen_at=?,message=?,action=? WHERE code=? AND resolved_at IS NULL', (now, p['message'], p['action'], p['code']))
        db.execute('INSERT OR REPLACE INTO health_state VALUES(1,?)', (json.dumps(report, ensure_ascii=False),))
        pending = db.execute("SELECT id,message FROM incidents WHERE resolved_at IS NULL AND acknowledged_at IS NULL AND coalesce(notification_status,'')!='requested' AND notification_attempt<=?", (stamp - 900,)).fetchall()
        # Reserve attempts before delivery so overlapping audits do not duplicate alerts.
        for row in pending:
            db.execute('UPDATE incidents SET notification_attempt=? WHERE id=?', (stamp, row['id']))
    if pending:
        status = notifier(f'{len(pending)}건 확인 필요: ' + pending[0]['message'] + ' http://127.0.0.1:8001/operations#pipeline-health')
        with closing(connect(store)) as db, db:
            db.executemany('UPDATE incidents SET notification_status=? WHERE id=?', [(status, r['id']) for r in pending])


def view(store=STORE):
    with closing(connect(store)) as db:
        row = db.execute('SELECT payload FROM health_state WHERE id=1').fetchone()
        report = json.loads(row[0]) if row else {'stages': [], 'models': MODELS}
        report['monitor_stale'] = age(report.get('checked_at'), time.time()) > 900
        report['incidents'] = [dict(r) for r in db.execute('SELECT * FROM incidents ORDER BY resolved_at IS NULL DESC,id DESC LIMIT 50')]
        return report


def acknowledge(incident_id, store=STORE):
    with closing(connect(store)) as db, db:
        return bool(db.execute('UPDATE incidents SET acknowledged_at=coalesce(acknowledged_at,?) WHERE id=?', (datetime.now(timezone.utc).isoformat(), incident_id)).rowcount)


def run(*, export=True, log=True):
    try:
        report = snapshot(ROOT / 'data/news.sqlite3')
    except Exception:
        # Do not persist exception text: third-party errors can contain private inputs.
        report = dict(checked_at=datetime.now(timezone.utc).isoformat(), stages=[], models=MODELS, problems=[dict(code='monitor_error', message='운영 상태 수집에 실패했습니다.', action='DB 접근과 운영 모니터 프로세스를 확인하세요.')])
    record(report)
    if export:
        target = ROOT / '.runtime/verification/operations-health.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(view(), ensure_ascii=False, indent=2))
    if log:
        print(json.dumps({'checked_at': report['checked_at'], 'problem_count': len(report['problems'])}), flush=True)


def watch(interval=2):
    """One bounded local monitor; no LLM calls or analysis dispatch."""
    import fcntl
    import signal
    import threading
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with (ROOT / '.runtime/operations-health-watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        next_export = 0
        while not stop.is_set():
            started = time.monotonic()
            export = started >= next_export
            run(export=export, log=export)
            if export:
                next_export = started + 30
            stop.wait(max(0.1, interval - (time.monotonic() - started)))
