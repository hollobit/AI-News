import json,sqlite3,time
from unittest.mock import patch
from source_enrichment import SourceService
from source_store import detail,search
from reach_pipeline import ReachPipeline,external_rows


def test_full_body_passages_versions_and_failed_refresh_preservation(tmp_path):
    path=tmp_path/'db';body='Early introduction.\n'*300+'\nQuasarFlux counterevidence late in the actual document.'
    response={'status':'fetched','text':body,'title':'Technical report','reader':'test','evidence_scope':'web_excerpt'}
    service=SourceService(path,fetcher=lambda _:dict(response))
    try:
        first=service.fetch('https://example.org/a')
        assert len(first['text'])==3500 and first['full_text_chars']==len(body)
        with sqlite3.connect(path) as db:
            evidence=search(db,'QuasarFlux counterevidence')
            assert evidence and evidence[0]['char_start']>3500
            e=evidence[0];assert e['text']==body[e['char_start']:e['char_end']]
            assert e['verification']=='unreviewed_source'
        response['text']=body+'\nChanged tail after the excerpt.'
        second=service.fetch('https://example.org/a',refresh=True)
        assert first['text']==second['text'] and first['full_content_hash']!=second['full_content_hash']
        response.update(status='failed',error='HTTP 429',text='')
        retained=service.fetch('https://example.org/a',refresh=True)
        assert retained['status']=='failed' and retained['text']==''
        assert retained['last_attempt_status']=='failed'
        with sqlite3.connect(path) as db:
            d=detail(db,'https://example.org/a')
            assert len(d['versions'])==2 and len(d['attempts'])==3
            assert not search(db,'QuasarFlux counterevidence')
            assert d['health']['last_success']
            assert d['health']['error_kind']=='rate_limit' and d['health']['next_retry']
    finally:service.close()


def test_pipeline_retry_dedup_scope_and_subscription_dates(tmp_path):
    path=tmp_path/'db'
    def reader(req):
        if req.get('mode')=='rss':return {'status':'fetched','entries':[{'url':'https://example.org/article','published_at':'2026-09-16T10:00:00+00:00'},{'url':'file:///private'}]}
        return {'status':'fetched','text':'QuasarFlux field observation with evidence','title':'Actual article','reader':'test','evidence_scope':'web_excerpt'}
    pipeline=ReachPipeline(path,autostart=False,reader=reader,reviewer=False)
    try:
        subscription=pipeline.add_subscription({'url':'https://example.org/feed','label':'Official','keywords':'QuasarFlux','interval_seconds':1})
        pipeline._schedule()
        with pipeline.db() as db:row=dict(db.execute('SELECT * FROM reach_tasks').fetchone())
        pipeline._work(row)
        with pipeline.db() as db:
            tasks=db.execute("SELECT * FROM reach_tasks WHERE kind='observation'").fetchall()
            assert len(tasks)==1
        pipeline._work(dict(tasks[0]))
        with pipeline.db() as db:
            rows=external_rows(db);assert len(rows)==1
            assert rows[0]['origin']=='external_watch' and rows[0]['day']=='2026-09-16'
            assert rows[0]['date_basis']=='article'
        assert pipeline.submit('observation',{},key=tasks[0]['task_key'])['id']==tasks[0]['id']
        assert pipeline.status()['full_documents']==1
        pipeline.toggle(subscription['id'],False)
        assert pipeline.status()['subscriptions'][0]['enabled']==0
    finally:pipeline.close()


def test_failure_backoff_and_no_private_or_dead_url_repair(tmp_path):
    path=tmp_path/'db';service=SourceService(path,fetcher=lambda u:{'status':'failed','error':'HTTP 404' if 'gone' in u else 'HTTP 429'})
    try:
        service.fetch('https://example.org/gone');service.fetch('https://example.org/limited')
    finally:service.close()
    pipeline=ReachPipeline(path,autostart=False,reader=lambda req:{'status':'failed','error':'HTTP 429'},reviewer=False)
    try:
        assert pipeline.repair()['queued']==0
        job=pipeline.submit('repair',{'url':'https://example.org/retry'})
        with pipeline.db() as db:row=dict(db.execute('SELECT * FROM reach_tasks WHERE id=?',(job['id'],)).fetchone())
        pipeline._work(row)
        final=pipeline.get(job['id'])
        assert final['status']=='retry' and final['next_at']>=time.time()+3500
    finally:pipeline.close()


def test_external_discovery_requires_fetched_sources_and_independent_review(tmp_path):
    path=tmp_path/'db'
    def reader(req):
        if req['action']=='search':return {'status':'fetched','entries':[{'url':'https://example.org/report'}]}
        return {'status':'fetched','text':'QuasarFlux has uncertain outcomes','title':'QuasarFlux report','reader':'test'}
    pipeline=ReachPipeline(path,autostart=False,reader=reader,reviewer=False)
    try:
        task=pipeline.research('QuasarFlux uncertainty','answer-job')
        with pipeline.db() as db:row=dict(db.execute('SELECT * FROM reach_tasks WHERE id=?',(task['id'],)).fetchone())
        with patch('graph_rag.answer_question',return_value={'answer':'Uncertain','verification':{'method':'independent_evidence_review'},'claims':[]}) as analyze:
            pipeline._work(row)
            refs=analyze.call_args.kwargs['retrieval']['evidence']
            assert refs[0]['text']=='QuasarFlux has uncertain outcomes'
        assert pipeline.get(task['id'])['result']['verified']
        assert pipeline.research('QuasarFlux uncertainty','answer-job')['id']==task['id']
    finally:pipeline.close()


def test_external_observation_enters_strategy_without_faking_telegram(tmp_path):
    from app import connect,read_news
    from strategy_views import dataset
    from improvement_selection import all_corpus_items
    path=tmp_path/'news.db'
    with connect(path):pass
    pipeline=ReachPipeline(path,autostart=False,reviewer=False)
    try:
        pipeline._observe('https://example.org/official',{'status':'fetched','title':'AI semiconductor supply','text':'반도체 semiconductor supply chain investment.'},'Official','2026-09-16T10:00:00+00:00')
        with connect(path) as db:
            rows=read_news(db,{'date':['all']})['items']
            assert len(rows)==1 and rows[0]['source_origin']=='external_watch'
            assert len(dataset(db)['items'])==1
            assert not all_corpus_items(db)
            assert db.execute('SELECT COUNT(*) FROM news').fetchone()[0]==0
    finally:pipeline.close()


def test_external_sources_are_included_in_risk_coverage_without_faking_origin():
    from risk_analysis import validate_risk_report
    import pytest
    evidence=[{'id':'external-1','origin':'external_source','text':'Actual public document'},
              {'id':'url-1','origin':'fetched_url_excerpt','text':'Fetched body'}]
    report={'summary':'Insufficient risk indicators','risks':[],'assessed_evidence_ids':[],
            'not_assessable_evidence_ids':['external-1','url-1'],'limitations':['No observed impact indicators.']}
    assert validate_risk_report(report,evidence)['not_assessable_evidence_ids']==['external-1','url-1']
    report['not_assessable_evidence_ids']=['url-1']
    with pytest.raises(ValueError):validate_risk_report(report,evidence)
