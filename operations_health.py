"""Local read-only health audit; no private content, credentials or remote messages."""
from datetime import datetime,timezone
import json,os,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def alive(pid):
    if not pid:return False
    try:os.kill(int(pid),0);return True
    except (ProcessLookupError,ValueError):return False
    except PermissionError:return True

def age(value,stamp):
    try:return stamp-datetime.fromisoformat(value).timestamp()
    except (TypeError,ValueError):return float('inf')

def audit(path,stamp=None):
    stamp=stamp if stamp is not None else datetime.now(timezone.utc).timestamp()
    alerts=[];result={}
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as db:
        db.row_factory=sqlite3.Row
        row=db.execute("SELECT value FROM state WHERE key='collector_last_success'").fetchone()
        checked=row[0] if row else None
        result['collector_last_success']=checked
        if age(checked,stamp)>300:alerts.append('Telegram 신규 메시지 확인이 5분 이상 갱신되지 않았습니다.')
        for kind,table in [('baseline','bulk_baseline_runs'),('deep','rsi_cycles')]:
            row=db.execute(f'SELECT status,owner_pid,updated_at FROM {table} ORDER BY created_at DESC LIMIT 1').fetchone()
            if row:
                result[kind]=dict(status=row['status'],owner_alive=alive(row['owner_pid']),updated_at=row['updated_at'])
                if row['status'] in ('paused','error','failed','requires_review','needs_review'):
                    alerts.append(kind+' 분석이 중단 또는 검토 대기 상태입니다. 자동으로 사용자 일시중지를 해제하지 않습니다.')
                elif row['status'] in ('running','preparing','finishing') and not alive(row['owner_pid']):
                    alerts.append(kind+' 실행 기록의 소유 프로세스가 없습니다.')
        result['paper_counts']=dict(db.execute('SELECT status,count(*) FROM arxiv_paper_analyses GROUP BY status'))
        if result['paper_counts'].get('failed',0) or result['paper_counts'].get('needs_review',0):
            alerts.append('논문 실패 또는 검토 대기가 남아 있습니다.')
    result.update(checked_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat(),alerts=alerts)
    return result

if __name__ == '__main__':
    from pipeline_health import run
    run()
