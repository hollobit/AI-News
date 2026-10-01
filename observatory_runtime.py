"""Background view preparation and replay of actual persisted processing events."""
from task_lifecycle import PreparationExecutor as ThreadPoolExecutor, checkpoint, cancellable_db
from pathlib import Path
import json
import sqlite3
import threading
import time
from observatory import read_observatory
from projection_cache import content_digest,revision_token


def processing_events(db, limit=200):
    limit=max(1,min(200,int(limit)));events=[]
    for kind,table in [('baseline','bulk_baseline_events'),('deep','strategic_workflow_events')]:
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
        rows=db.execute(f'SELECT seq,run_id,stage,detail,created_at FROM {table} ORDER BY seq DESC LIMIT ?', (limit,))
        events.extend(dict(id=f'{kind}:{r[0]}',kind=kind,run_id=r[1],stage=r[2],detail=r[3],created_at=r[4]) for r in rows)
    events=sorted(events,key=lambda e:(e['created_at'],e['id']))[-limit:]
    return {'events':events,'version':content_digest(events),'scope':'실제 저장된 기본·심층 분석 처리 기록 중 최근 200건. 뉴스 관측 날짜와 별개입니다.'}


class ObservatoryRuntime:
    def __init__(self,path,status_loader=None,*,isolated=False):
        self.isolated=isolated
        self.path=str(path);self.lock=threading.RLock();self.views={};self.pending=set();self.checked={};self.errors={}
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='observatory-views')
        self.directory=Path(str(path)+'.observatory');self.directory.mkdir(exist_ok=True)
        self.closed=False
        self.status_loader=status_loader;self.status_value={};self.status_pending=False;self.status_checked=0
        self.status_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="observatory-status")

    def request(self,window=14,expanded=False):
        if window not in (14,30,90):raise ValueError('관측 기간은 14, 30, 90일 중 선택해 주세요.')
        variant='expanded' if expanded else 'default'
        view_key=(window,expanded)
        with self.lock:
            if view_key not in self.views:
                file=self.directory/f'{window}-{variant}-v2.json'
                legacy=self.directory/f'{window}-v2.json'
                try:
                    candidates=[file]+([] if expanded else [legacy])
                    for candidate in candidates:
                        if not candidate.exists() or candidate.stat().st_size>=40_000_000:continue
                        saved=json.loads(candidate.read_text())
                        if saved.get('window')==window and saved.get('expanded',False)==expanded and len(saved.get('data',{}).get('days',[]))==window:
                            self.views[view_key]=saved['data'];break
                except (ValueError,OSError,TypeError):pass
            if not self.closed and view_key not in self.pending and time.monotonic()-self.checked.get(view_key,0)>15:
                self.pending.add(view_key);self.pool.submit(self._prepare,window,expanded)
            result=dict(self.views.get(view_key) or {})
            result['refreshing']=view_key in self.pending
            if self.errors.get(view_key):result['refresh_error']=self.errors[view_key]
            if not result.get('days'):result['status']='preparing'
            return result

    def _prepare(self,window,expanded=False):
        view_key=(window,expanded);variant='expanded' if expanded else 'default'
        try:
            file=self.directory/f'{window}-{variant}-v2.json'
            if view_key not in self.views and file.exists():
                try:
                    saved=json.loads(file.read_text())
                    if saved.get('window')==window and saved.get('expanded',False)==expanded and isinstance(saved.get('data',{}).get('days'),list):
                        with self.lock:self.views[view_key]=saved['data']
                except (ValueError,OSError):pass
            if self.isolated:
                from projection_worker import prepare
                prepare('observatory',self.path,window,int(expanded),file)
                result=json.loads(file.read_text())['data']
            else:
                with sqlite3.connect(self.path,timeout=2) as db:
                    db.row_factory=sqlite3.Row
                    cancellable_db(db)
                    result=read_observatory(db,window,expanded)
                checkpoint()
                temp=file.with_suffix('.tmp');temp.write_text(json.dumps({'window':window,'expanded':expanded,'data':result},ensure_ascii=False));temp.replace(file)
            with self.lock:self.views[view_key]=result;self.errors.pop(view_key,None)
        except Exception as error:
            with self.lock:self.errors[view_key]='관측 자료 재집계 실패: '+type(error).__name__
        finally:
            with self.lock:self.pending.discard(view_key);self.checked[view_key]=time.monotonic()

    def status(self):
        with self.lock:
            if self.status_loader and not self.closed and not self.status_pending and time.monotonic()-self.status_checked>10:
                self.status_pending=True;self.status_pool.submit(self._status)
            return dict(self.status_value,refreshing=self.status_pending)

    def _status(self):
        try:
            value=self.status_loader()
            from datetime import datetime,timezone
            value['computed_at']=datetime.now(timezone.utc).isoformat()
            with self.lock:self.status_value=value
        except Exception as error:
            with self.lock:self.status_value=dict(self.status_value,error='상태 갱신 실패: '+type(error).__name__)
        finally:
            with self.lock:self.status_pending=False;self.status_checked=time.monotonic()

    def events(self):
        with sqlite3.connect(self.path,timeout=.25) as db:return processing_events(db)

    def close(self):
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True);self.status_pool.shutdown(wait=False,cancel_futures=True)
