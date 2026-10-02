"""Source-first bounded hints for corpus analysis, not the full GraphRAG UI."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from verified_cache import policy
from graph_quick_sources import cold_answer
from graph_rag import GraphResult, _workflow_graph, build_retrieval
from completion_quality import document_admission
from source_enrichment import attach_sources
from evidence_corrections import correction_token
from projection_cache import cached_read, content_digest
from link_groups import canonical_url


def retrieve(path, question):
    started=time.monotonic()
    shortlist=cold_answer(path,question,[],{},budget=2,limit=24,ranked=True,include_items=True)
    lookup_ms=round((time.monotonic()-started)*1000,2)
    nodes=[];edges=[];evidence=[];validated=0;jobs=[]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cycle=db.execute('SELECT id FROM rsi_cycles ORDER BY created_at DESC LIMIT 1').fetchone() if 'rsi_cycles' in tables else None
        corrections=correction_token(db)
        for candidate in (shortlist or {}).get('evidence',[]):
            item=candidate['current_item']
            fresh=db.execute('SELECT a.rowid AS search_rowid,a.*,n.channel FROM articles a JOIN news n ON a.chat_id=n.chat_id AND a.message_id=n.message_id WHERE a.rowid=?',(item['search_rowid'],)).fetchone()
            if fresh is None or dict(fresh)!=item:continue
            current=attach_sources(db,[item])[0] if 'source_excerpts' in tables else item
            raw={k:v for k,v in candidate.items() if k!='current_item'}
            raw['source_kind']='stored_news';raw['basis']='unreviewed_source_excerpt'
            evidence.append(raw)
            if not cycle or not {'corpus_completion_documents','strategic_workflow_runs','strategic_workflow_artifacts'}<=tables:continue
            url=canonical_url(item.get('source_url') or '')
            if not url:continue
            row=db.execute('''SELECT r.id,r.status,r.error,a.payload_json FROM corpus_completion_documents d
                JOIN strategic_workflow_runs r ON r.id=d.workflow_run_id
                JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final'
                WHERE d.cycle_id=? AND d.document_id=? AND d.status='complete'
                AND r.status='complete' AND COALESCE(r.error,'')='' ''',(cycle[0],url)).fetchone()
            if not row:continue
            jobs.append((dict(row),current))
        # Copy selected inputs under one read snapshot, then release it. The
        # cache can single-flight pure validation of these exact copied bytes.
        db.commit()
        for row,current in jobs:
            def project():
                payload=json.loads(row['payload_json'])
                run=dict(id=row['id'],status=row['status'],error=row['error'],results=payload)
                if not document_admission(run,current)['complete']:return {}
                return _workflow_graph(payload,row['id'],corrections)
            key=content_digest([policy(),row['id'],row['payload_json'],current,corrections])
            graph=cached_read(db,'workflow_hint_graph',key,project,copy_result=False)
            if not graph:continue
            validated+=1
            # Local evidence IDs overlap between workflows; namespace every reference.
            prefix=row['id']+':'
            for n in graph.get('nodes',[]):
                nodes.append(dict(n,id=prefix+n['id'],evidence_ids=[prefix+x for x in n['evidence_ids']]))
            for e in graph.get('edges',[]):
                edges.append(dict(e,id=prefix+content_digest(e),source=prefix+e['source'],target=prefix+e['target'],evidence_ids=[prefix+x for x in e['evidence_ids']]))
            for e in graph.get('evidence',[]):
                evidence.append(dict(e,id=prefix+e['id'],source_kind='workflow',workflow_run_ids=[row['id']]))
    result=build_retrieval(GraphResult(nodes=nodes,edges=edges,evidence=evidence),question)
    result['limitations'] = ['기사 분석용 검색 단서: 뉴스 색인 일치 후보 최대300건에서 관련24건과 현재 검토 통과 분석만 조회합니다. 전체 논문·지식 그래프 탐색이 아닙니다. 원문 관측과 검토된 해석을 구분하며 현재 기사 근거를 대신하지 않습니다.']
    result['retrieval'].update(method='workflow-source-first-v1',source_lookup_ms=lookup_ms,validated_workflows=validated,total_ms=round((time.monotonic()-started)*1000,2))
    return result


def input_revision(db):
    from projection_cache import revision_token
    from verified_cache import policy
    token=revision_token(db)
    # Unknown revisions are never reusable checkpoints.
    return ['workflow-source-first-v1',policy(),token if token is not None else time.time_ns(),correction_token(db)]
