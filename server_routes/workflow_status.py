"""Read-only, bounded operational snapshot; never executes analysis or validation."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from operations_health import alive, age
from engine_errors import infrastructure_error, MESSAGES


def snapshot(path):
    stamp=time.time()
    stages=[];events=[];tasks=[]
    def stage(identity,title,counts=None,done=(),**extra):
        counts=counts or {};total=sum(counts.values());complete=sum(counts.get(s,0) for s in done)
        value=dict(id=identity,title=title,counts=counts,total=total,complete=complete,
                   percent=math.floor(1000*complete/total)/10 if total else None,**extra)
        stages.append(value);return value
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def counts(table,where='',args=()):
            return dict(db.execute('SELECT status,count(*) FROM '+table+where+' GROUP BY status',args)) if table in tables else {}
        states=dict(db.execute("SELECT key,value FROM state WHERE key IN ('collector_last_success','collector_corpus_snapshot')")) if 'state' in tables else {}
        checked=states.get('collector_last_success')
        stage('collector','Telegram 확인',status='live' if age(checked,stamp)<120 else 'delayed',updated_at=checked,
              note='신규·수정 메시지 확인 주기입니다. 완료율을 계산하지 않습니다.',href='/operations#pipeline-health',unit='확인 주기')
        try:extraction=json.loads(states.get('collector_corpus_snapshot','{}'))
        except ValueError:extraction={}
        stage('extract','기사 추출·중복 제거',status=extraction.get('status','unknown'),updated_at=extraction.get('extracted_at'),
              observed=extraction.get('total_unique'),note='추출이 끝난 시점의 고유 뉴스 수입니다. 논문 수는 포함하지 않습니다.',href='/news',unit='고유 뉴스')
        for identity,title,table,ledger,key,done,href in [
            ('baseline','기본 분석·독립 검토','bulk_baseline_runs','bulk_baseline_documents','run_id',('verified',),'/operations#baseline'),
            ('deep','상세·위험 검토','rsi_cycles','corpus_completion_documents','cycle_id',('complete',),'/operations#improvement')]:
            row=db.execute('SELECT id,status,owner_pid,updated_at,error FROM '+table+' ORDER BY created_at DESC LIMIT 1').fetchone() if table in tables else None
            values=counts(ledger,' WHERE '+key+'=?',(row['id'],)) if row else {}
            item=stage(identity,title,values,done,status=row['status'] if row else 'unknown',
                       owner_alive=alive(row['owner_pid']) if row else False,run_id=row['id'] if row else None,
                       updated_at=row['updated_at'] if row else None,href=href,unit='기사',
                       note='최신 실행 원장 기준입니다. 입력 변경 시 완료 건이 재검토로 전환될 수 있습니다.')
            if row and row['status']=='running' and not item['owner_alive']:item['status']='owner_missing'
            if row:
                code=infrastructure_error(row['error'])
                item['error_message']=MESSAGES.get(code,'')
            if row and identity=='deep':
                if 'completion_engine_waits' in tables:
                    item['retries']=[dict(r) for r in db.execute('''SELECT r.number,w.failures,w.next_attempt_at FROM completion_engine_waits w
                        JOIN rsi_rounds r ON r.id=w.round_id WHERE r.cycle_id=? AND r.status IN ('planned','running')
                        ORDER BY w.next_attempt_at LIMIT 10''',(row['id'],))]
                if 'rsi_rounds' in tables:
                    for r in db.execute('SELECT number,status,created_at,completed_at FROM rsi_rounds WHERE cycle_id=? ORDER BY number DESC LIMIT 12',(row['id'],)):
                        events.append(dict(r))
                    if 'strategic_workflow_events' in tables:
                        for r in db.execute("""SELECT number,status,workflow_run_id,
                            json_extract(snapshot_json,'$.items[0].title') AS title FROM rsi_rounds
                            WHERE cycle_id=? AND status IN ('running','planned') ORDER BY number LIMIT 6""", (row['id'],)):
                            task=dict(r)
                            latest=db.execute("SELECT stage,status,created_at FROM strategic_workflow_events WHERE run_id=? AND stage NOT LIKE 'model_call_%' ORDER BY seq DESC LIMIT 1", (r['workflow_run_id'],)).fetchone()
                            task['latest']=dict(latest) if latest else None
                            tasks.append(task)
        for identity,title,table,done,href,note in [
            ('sources','외부 원문 확보','source_health',('fetched',),'/sources','확보 상태입니다. 원문 사실의 독립 검토 완료율이 아닙니다.'),
            ('papers','논문 분석','arxiv_paper_analyses',('complete',),'/papers','등록된 논문 분석 행 기준입니다. 최신 입력 유효성은 논문 화면에서 확인합니다.'),
            ('wiki','지식 위키 편찬','wiki_topics',('complete',),'/wiki','등록된 주제의 편찬 상태입니다. 모든 기사의 위키 편찬 비율이 아닙니다.')]:
            values=dict(db.execute('SELECT last_status,count(*) FROM source_health GROUP BY last_status')) if table=='source_health' and table in tables else counts(table)
            stage(identity,title,values,done,status='recorded' if values else 'unknown',note=note,href=href,unit='원문' if identity=='sources' else '논문' if identity=='papers' else '주제')
        if 'risk_schedule' in tables:
            schedule=dict(db.execute('SELECT enabled,recoveries,stagnant,next_attempt_at FROM risk_schedule WHERE id=1').fetchone() or {})
        else:schedule={}
    runtime_dir=Path(path).resolve().parent.parent/'.runtime'
    try:
        dispatch=json.loads((runtime_dir/'scheduled-risks.json').read_text())
        schedule.update(stage=dispatch.get('stage'),checked_at=dispatch.get('checked_at'))
    except (OSError,ValueError):pass
    marker=runtime_dir/'public-site.publication.json'
    published=None
    try:
        raw=json.loads(marker.read_text());published={'commit':str(raw.get('commit',''))[:12],'recorded_at':datetime.fromtimestamp(marker.stat().st_mtime,timezone.utc).isoformat()}
    except (OSError,ValueError):pass
    stage('publication','공개 사이트 게시',status='recorded' if published else 'unknown',updated_at=published['recorded_at'] if published else None,
          note='마지막 게시 도구 기록입니다. 원격 배포 성공이나 현재 전체 분석 완료를 뜻하지 않습니다.',href='/wiki',unit='게시 기록',publication=published)
    calls=[];limit=None;runtime_error=False;call_counts={'running':0,'queued':0}
    runtime=Path(path).parent/'llm_runtime.sqlite3'
    if runtime.exists():
        try:
            with closing(sqlite3.connect(runtime.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
                db.row_factory=sqlite3.Row
                limit=db.execute('SELECT max_concurrent FROM llm_runtime_settings WHERE id=1').fetchone()[0]
                for r in db.execute("SELECT owner_pid,status,COUNT(*) AS n FROM llm_calls WHERE status IN ('queued','running') GROUP BY owner_pid,status"):
                    if alive(r['owner_pid']):call_counts[r['status']]+=r['n']
                calls=[dict(r) for r in db.execute('''SELECT id,role,model,status,owner_pid,started_at,queued_at,finished_at,error_code
                    FROM llm_calls WHERE status IN ('queued','running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,id DESC LIMIT 12''')]
                for call in calls:
                    call['owner_alive']=alive(call.pop('owner_pid'));call['elapsed_seconds']=max(0,round(stamp-(call['started_at'] or call['queued_at'])))
                    if not call['owner_alive']:call['status']='owner_missing'
        except sqlite3.Error:runtime_error=True
    return dict(observed_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat(),stages=stages,events=events,tasks=tasks,
                calls=calls,call_counts=call_counts,call_limit=limit,runtime_error=runtime_error,schedule=schedule,
                scope='각 단계의 원장·확보 기록입니다. 서로 다른 대상을 합산한 전체 완료율은 제공하지 않습니다.')
