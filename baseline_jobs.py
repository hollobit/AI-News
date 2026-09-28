"""Baseline HTTP facade over durable run records and a detached worker."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from bulk_baseline import BulkBaselineService, now
from recursive_improvement import owner_alive


class BaselineJobs:
    def __init__(self,path,selector):
        self.path=str(Path(path).resolve())
        self.reader=BulkBaselineService(self.path,selector)
        self.enabled=self.reader.enabled
        self.lock=self.reader.lock
        self.active=None;self.active_batches=0

    def get(self,run_id):
        result=self.reader.get(run_id)
        if result:result['metrics']['active_workers']=self.worker_counts().get(run_id,0)
        return result

    def list(self,limit=12):
        return [self.get(r['id']) for r in self.reader.list(limit)]

    def worker_counts(self):
        with self.reader.db() as db:
            rows=db.execute("SELECT r.id,r.owner_pid,r.settings_json,COUNT(d.document_id) AS count FROM bulk_baseline_runs r LEFT JOIN bulk_baseline_documents d ON d.run_id=r.id AND d.status='running' WHERE r.status IN ('preparing','running','finishing') GROUP BY r.id").fetchall()
        return {r['id']:math.ceil(r['count']/max(1,json.loads(r['settings_json']).get('batch_size',1))) for r in rows if owner_alive(r['owner_pid'])}

    def _launch(self,action,run_id='',settings=None):
        self.reader._available()
        root=Path(__file__).resolve().parent
        directory=root/'.runtime'/'baseline-worker';directory.mkdir(parents=True,exist_ok=True)
        ready=directory/(uuid.uuid4().hex+'.json')
        command=[sys.executable,str(root/'baseline_worker.py'),'--db',self.path,'--action',action,'--run-id',run_id,'--settings',json.dumps(settings or {}),'--ready',str(ready)]
        with (directory/'worker.log').open('ab') as log:
            process=subprocess.Popen(command,cwd=root,stdout=log,stderr=log,start_new_session=True)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if ready.exists():
                try:value=json.loads(ready.read_text())
                except json.JSONDecodeError:
                    time.sleep(.05);continue
                ready.unlink()
                if value.get('error_type'):raise RuntimeError('기본 분석 worker를 시작하지 못했습니다: '+value['error_type'])
                return self.get(value['run']['id'])
            if process.poll() is not None:raise RuntimeError('기본 분석 worker가 준비 중 종료되었습니다.')
            time.sleep(.05)
        # A timed-out request must not kill or relaunch a worker that owns a run.
        raise RuntimeError('기본 분석 준비가 지연됩니다. 실행 상태를 먼저 확인해 주세요.')

    def start(self,settings=None):
        with self.lock:return self._launch('start',settings=settings)

    def resume(self,run_id):
        if not self.get(run_id):raise ValueError('기본 분석 실행을 찾을 수 없습니다.')
        if self.get(run_id)['status']=='complete':return self.get(run_id)
        with self.lock:return self._launch('resume',run_id)

    def pause(self,run_id):
        with self.reader.db() as db:
            row=db.execute('SELECT owner_pid,status FROM bulk_baseline_runs WHERE id=?',(run_id,)).fetchone()
            if not row:raise ValueError('기본 분석 실행을 찾을 수 없습니다.')
            alive=owner_alive(row['owner_pid'])
            db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',(run_id,'user_pause_requested','{}',now()))
            db.execute('UPDATE bulk_baseline_runs SET status=?,updated_at=? WHERE id=?',('finishing' if alive else 'paused',now(),run_id))
        return self.get(run_id)

    def close(self):
        # This reader owns no analysis thread. The detached worker checkpoints
        # only when a durable pause request or a signal reaches that worker.
        self.reader.close()
