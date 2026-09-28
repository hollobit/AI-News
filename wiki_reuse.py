"""Reuse current reviewed analysis as editing hints, never as extra evidence."""
import json
from link_groups import canonical_url
from wiki_sources import exists


def reviewed_hints(db, bundle):
    news = {canonical_url(e.get('source_url') or ''):e['id'] for e in bundle['evidence']
            if e['dependency']['kind']=='news' and e.get('source_url')}
    hints = []
    required = ('news','articles','archived_urls','bulk_baseline_cache')
    if news and all(exists(db,t) for t in required):
        from improvement_selection import all_corpus_items
        from baseline_graph import baseline_sources
        items = all_corpus_items(db,source_urls=set(news))
        baselines,_ = baseline_sources(db,items)
        for source in baselines:
            refs = sorted({news[canonical_url(e['source_url'])] for e in source['result']['evidence']
                           if canonical_url(e['source_url']) in news})
            for node in source['result']['nodes']:
                if node['type']=='NewsSummary':hints.append(dict(kind='baseline',text=node['summary'],evidence_ids=refs))
        if all(exists(db,t) for t in ('corpus_completion_documents','strategic_workflow_runs','strategic_workflow_artifacts')):
            from completion_quality import document_admission, message_text
            from graph_rag import validated_workflow_content
            for item in items:
                url=canonical_url(item.get('source_url') or '')
                row=db.execute('''SELECT r.*,a.payload_json FROM corpus_completion_documents d
                    JOIN strategic_workflow_runs r ON r.id=d.workflow_run_id
                    JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final'
                    WHERE d.document_id=? AND r.status='complete' AND r.error=''
                    ORDER BY r.updated_at DESC LIMIT 1''',(url,)).fetchone()
                if not row:continue
                result=json.loads(row['payload_json'])
                if not document_admission(dict(row,results=result),item)['verified']:continue
                content=validated_workflow_content(result,row['id'])
                own={e['id'] for e in result['evidence'] if canonical_url(e.get('url') or '')==url
                     and ((e.get('origin')=='telegram_excerpt' and e['text']==message_text(item))
                          or (e.get('origin')=='fetched_url_excerpt' and e['text']==str((item.get('source_context') or {}).get('text') or '')[:3500]))}
                for claim in content.get('claims',[]):
                    if set(claim['evidence_ids'])<=own:
                        hints.append(dict(kind='deep',text=claim['detail']+' / '+claim['uncertainty'],evidence_ids=[news[url]]))
    for e in bundle['evidence']:
        if e['dependency']['kind']!='paper':continue
        from wiki_sources import paper_token
        if paper_token(db,e['dependency']['key'])!=e['dependency']['token']:continue
        row=db.execute('SELECT result_json FROM arxiv_paper_analyses WHERE paper_id=?',(e['dependency']['key'],)).fetchone()
        result=json.loads(row[0])
        for c in result['report']['claims'][:4]:hints.append(dict(kind='paper',text=c['detail']+' / '+c['uncertainty'],evidence_ids=[e['id']]))
    return hints[:32]
