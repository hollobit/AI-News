"""Wake the single publisher for committed content changes, not job heartbeats."""
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path

FIELDS = {
    'news': None, 'articles': None, 'archived_urls': None,
    'source_health': ('url','hash','last_status'),
    'source_versions': ('url','hash','title','text'),
    'source_excerpts': ('canonical_url','result_json'),
    'repaired_source_links': None,
    'bulk_baseline_cache': ('input_hash','document_id','result_json','evidence_json','prepared_json'),
    'bulk_baseline_documents': ('run_id','document_id','input_hash','snapshot_json','status','result_json','error'),
    'corpus_completion_documents': ('cycle_id','document_id','status','workflow_run_id','error'),
    'strategic_workflow_runs': ('id','status','snapshot_json','request_json','error','updated_at'),
    'strategic_workflow_artifacts': None,
    'arxiv_papers': ('paper_id','metadata_json','status'),
    'arxiv_paper_mentions': None,
    'arxiv_paper_analyses': ('paper_id','input_hash','status','error','result_json'),
    'wiki_topics': ('id','status','input_hash','error','published_revision','policy_hash'),
    'wiki_revisions': None, 'wiki_structures': None, 'wiki_aliases': None,
    'wiki_settings': ('id','enabled','settings_json'),
    'intel_records': ('id','kind','version','payload_json'),
    'intel_entities': None, 'intel_assessments': None, 'intel_aliases': None,
    'intel_exclusions': None, 'intel_event_assignments': None,
    'risk_review_resolutions': None,
}


def quote(name):
    return '"'+name.replace('"','""')+'"'


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS publication_input_revision(
        id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,layout TEXT NOT NULL DEFAULT '')''')
    db.execute('INSERT OR IGNORE INTO publication_input_revision(id,epoch) VALUES(1,?)',(uuid.uuid4().hex,))
    existing=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"));layout={};changed=False
    for table,wanted in FIELDS.items():
        columns=tuple(r[1] for r in db.execute('PRAGMA table_info('+quote(table)+')'))
        fields=columns if wanted is None else tuple(f for f in wanted if f in columns)
        if fields:layout[table]=fields
    for table,fields in layout.items():
        for event in ('INSERT','UPDATE','DELETE'):
            name='publication_input_v1_'+table+'_'+event.lower()
            conditions=[]
            refs=('OLD','NEW') if event=='UPDATE' else ('OLD',) if event=='DELETE' else ('NEW',)
            if event=='UPDATE':conditions.append('('+' OR '.join('OLD.'+quote(f)+' IS NOT NEW.'+quote(f) for f in fields)+')')
            if table=='strategic_workflow_runs' and 'status' in fields:
                conditions.append('('+' OR '.join(p+".status='complete'" for p in refs)+')')
            if table=='strategic_workflow_artifacts' and 'stage' in fields:
                conditions.append('('+' OR '.join(p+".stage='final'" for p in refs)+')')
            if table=='source_versions' and {'url','hash'}<=set(layout.get('source_health',())):
                conditions.append('('+' OR '.join('EXISTS(SELECT 1 FROM source_health h WHERE h.url='+p+'.url AND h.hash='+p+'.hash)' for p in refs)+')')
            sql=('CREATE TRIGGER '+name+' AFTER '+event+' ON '+quote(table)
                +(' WHEN '+' AND '.join(conditions) if conditions else '')
                +' BEGIN UPDATE publication_input_revision SET revision=revision+1 WHERE id=1; END')
            if existing.get(name)!=sql:
                db.execute('DROP TRIGGER IF EXISTS '+name);db.execute(sql);changed=True
    encoded=json.dumps(layout,sort_keys=True)
    if changed or db.execute('SELECT layout FROM publication_input_revision WHERE id=1').fetchone()[0]!=encoded:
        db.execute('UPDATE publication_input_revision SET revision=revision+1,layout=? WHERE id=1',(encoded,))


class Changes:
    def __init__(self,path,root):
        self.path,self.root=Path(path),Path(root);self.schema=None

    def read(self):
        with sqlite3.connect(self.path,timeout=.5) as db:
            schema=db.execute('PRAGMA schema_version').fetchone()[0]
            if schema!=self.schema:
                install(db);self.schema=db.execute('PRAGMA schema_version').fetchone()[0]
            token=tuple(db.execute('SELECT epoch,revision FROM publication_input_revision WHERE id=1').fetchone())
        # Saved observations, UI, code and the public origin are not DB rows.
        # Compare file identity/timestamps only; never read runtime credentials.
        files=list(self.root.glob('*.py'))+list((self.root/'static').glob('*'))
        files+=list(Path(str(self.path)+'.observatory').glob('*-v2.json'))
        files+=[self.root/'.runtime/remote-access/public.json']
        versions=[]
        for path in sorted(files):
            try:
                stat=path.stat()
                if path.is_file():versions.append((str(path),stat.st_ino,stat.st_size,stat.st_mtime_ns))
            except FileNotFoundError:pass
        return token,tuple(versions)


class Schedule:
    """Changes during an export remain pending; a failure never tight-loops."""
    def __init__(self,*,clock=time.monotonic,quiet=10,maximum=45,fallback=300):
        if not 0<quiet<=maximum<=fallback:raise ValueError('Invalid publication intervals')
        self.clock=clock;self.quiet=quiet;self.maximum=maximum;self.fallback=fallback
        self.token=None;self.first=self.last=None;self.next_check=clock();self.retry_at=0
        self.active=False

    def observe(self,token):
        now=self.clock()
        if self.token is not None and token!=self.token:
            if self.first is None:self.first=now
            self.last=now
        self.token=token

    def due(self):
        now=self.clock()
        change_at=min(self.first+self.maximum,self.last+self.quiet) if self.first is not None else math.inf
        return not self.active and now>=self.retry_at and now>=min(self.next_check,change_at)

    def started(self):
        self.active=True;self.first=self.last=None

    def finished(self,success):
        self.active=False;self.next_check=self.clock()+self.fallback
        self.retry_at=0 if success else self.next_check

    def view(self):
        now=self.clock()
        deadline=min(self.first+self.maximum,self.last+self.quiet) if self.first is not None else self.next_check
        return dict(state='publishing' if self.active else 'retry_wait' if now<self.retry_at else 'coalescing' if self.first is not None else 'watching',
                    remaining_seconds=max(0,math.ceil(max(self.retry_at,deadline)-now)),
                    quiet_seconds=self.quiet,max_wait_seconds=self.maximum,fallback_seconds=self.fallback)


def state_path(output):
    p=Path(output).absolute();return p.parent/(p.name+'.publisher.json')


def view(output,stamp=None):
    from operations_health import alive
    stamp=time.time() if stamp is None else stamp
    try:
        raw=json.loads(state_path(output).read_text())
        state=raw['state']
        if state not in ('publishing','retry_wait','coalescing','watching'):return None
        healthy=alive(raw['pid']) and 0<=stamp-raw['checked_at']<15
        result=dict(state=state if healthy and not raw.get('error_type') else 'unavailable')
        if result['state'] in ('coalescing','retry_wait'):
            result['remaining_seconds']=max(0,int(raw['remaining_seconds']))
        return result
    except (OSError,ValueError,KeyError,TypeError):return None


def watch(db,output,*,stop=None):
    """Caller owns the existing publisher lock; there is at most one child."""
    import os
    import subprocess
    import sys
    import threading
    from worker_health import write_state
    stop=stop or threading.Event();changes=Changes(db,Path(__file__).resolve().parent);schedule=Schedule()
    child=None;error_type=None;last_result=None
    while not stop.is_set() or child is not None:
        try:
            schedule.observe(changes.read());error_type=None
        except (OSError,sqlite3.Error) as error:error_type=type(error).__name__
        if child is not None and child.poll() is not None:
            success=child.returncode==0;schedule.finished(success);child=None
            last_result='finished' if success else 'failed'
        if not stop.is_set() and child is None and error_type is None and schedule.due():
            schedule.started()
            try:
                child=subprocess.Popen([sys.executable,str(Path(__file__).resolve().with_name('sync_wiki_pages.py')),
                    '--db',str(db),'--output',str(output),'--child'])
            except OSError as error:
                error_type=type(error).__name__;schedule.finished(False);last_result='failed'
        write_state(state_path(output),dict(schedule.view(),pid=os.getpid(),child_pid=child.pid if child else None,
            checked_at=time.time(),error_type=error_type,last_result=last_result,stopping=stop.is_set()))
        if stop.is_set():
            # Keep the lock while an in-flight commit/ref update finishes.
            if child is not None:time.sleep(.2)
        else:stop.wait(2)
