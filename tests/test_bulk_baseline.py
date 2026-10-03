import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from bulk_baseline import BulkBaselineService,read_baseline,freeze_item


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'news.db';self.services=[]
        self.items=[{'id':i,'title':f'소버린 AI 정부 투자 {i}','text':'정부 소버린 AI 인프라 사업','source_url':f'https://example.com/{i}'} for i in range(7)]
        self.calls=[];self.bad_ids=set();self.reject_ids=set()

    def tearDown(self):
        for s in self.services:
            s.close()
            if s.thread:s.thread.join(5)
        self.temp.cleanup()

    def model(self,prompt,schema):
        self.calls.append(prompt);data=json.loads(prompt.split('DATA:\n')[1])
        if prompt.startswith('ROLE: baseline_verification'):
            return {'reviews':[{'document_id':d['document_id'],'accepted':d['document_id'] not in self.reject_ids,
                        'issues':['과장'] if d['document_id'] in self.reject_ids else [],'checked_evidence_ids':[e['id'] for e in d['evidence']]} for d in data]}
        return {'documents':[{'document_id':d['document_id'],'summary':'정부 인프라 투자 소식','keywords':[{'label':'소버린 AI','source_quote':'소버린 AI'}],
                   'strategic_relevance':'인프라 수요 관찰','risk_signal':'시행 여부 미확인','limitations':'메시지 발췌 범위',
                   'evidence_ids':['fake'] if d['document_id'] in self.bad_ids else [d['evidence'][0]['id']]} for d in data]}

    def service(self,model=None):
        s=BulkBaselineService(self.path,lambda:self.items,analyzer=model or self.model,enabled=True,
               extractor=lambda text:[{'label':'소버린 AI','surface':'소버린 AI','kind':'technical_dictionary'}])
        self.services.append(s);return s

    def done(self,s,id):
        end=time.monotonic()+5
        while time.monotonic()<end:
            r=s.get(id)
            if r['status'] in ('complete','requires_review','failed','paused'):
                if s.thread:s.thread.join(2)
                return r
            time.sleep(.01)
        self.fail(str(s.get(id)))

    def test_every_document_batched_verified_and_reused(self):
        s=self.service();r=self.done(s,s.start({'batch_size':3,'workers':3})['id'])
        self.assertEqual(r['status'],'complete',r['error']);self.assertEqual(r['metrics']['verified'],7)
        self.assertEqual(r['metrics']['preprocessed'],7)
        batches=[json.loads(p.split('DATA:\n')[1]) for p in self.calls if p.startswith('ROLE: baseline_analysis')]
        self.assertEqual(sorted(map(len,batches)),[1,3,3])
        count=len(self.calls);again=self.done(s,s.start({'batch_size':3})['id'])
        self.assertEqual(again['metrics']['reused_verified'],7);self.assertEqual(len(self.calls),count)
        with s.db() as db:self.assertEqual(len(read_baseline(db,self.items)),7)

    def test_review_receives_same_keyword_candidates_as_generation(self):
        service=self.service()
        result=self.done(service,service.start({'workers':1,'batch_size':1})['id'])
        self.assertEqual(result['status'],'complete')
        generated={d['document_id']:d['keyword_citations'] for p in self.calls if p.startswith('ROLE: baseline_analysis') for d in json.loads(p.split('DATA:\n')[1])}
        for prompt in self.calls:
            if prompt.startswith('ROLE: baseline_verification'):
                for document in json.loads(prompt.split('DATA:\n')[1]):
                    self.assertIn('최종 저장 형식',prompt)
                    self.assertNotIn('citation_id만 넣는다',prompt.split('DATA:')[0])
                    self.assertEqual(document['keyword_citations'],generated[document['document_id']])
                    self.assertTrue(document['keyword_citations'])
                    for keyword in document['analysis']['keywords']:
                        self.assertIn(keyword,document['keyword_citations'].values())

    def test_engine_outage_pauses_without_exhausting_documents(self):
        def unavailable(prompt, schema):
            raise RuntimeError('[engine:circuit_open] temporary outage')
        service = self.service(unavailable)
        state = self.done(service, service.start({'batch_size':2, 'workers':1})['id'])
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['metrics']['failed'], 0)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT sum(attempts) FROM bulk_baseline_documents').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM bulk_baseline_events WHERE stage='engine_paused'").fetchone()[0], 1)
        service.analyzer = self.model
        self.assertEqual(self.done(service, service.resume(state['id'])['id'])['metrics']['verified'], 7)

    def test_adaptive_batches_cover_all_documents_and_record_metrics(self):
        self.items=[dict(self.items[0],id=i,source_url=f'https://example.com/{i}') for i in range(35)]
        s=self.service();r=self.done(s,s.start({'adaptive_batches':True})['id'])
        self.assertEqual(r['settings']['batch_size'],12)
        self.assertEqual(r['metrics']['verified'],35)
        with s.db() as db:
            details=[json.loads(row[0]) for row in db.execute("SELECT detail FROM bulk_baseline_events WHERE stage='analysis_and_verification'")]
        self.assertEqual(sorted(d['documents'] for d in details),[3,16,16])
        self.assertEqual(sum(d['verified'] for d in details),35)
        for detail in details:
            self.assertEqual(detail['batch_mode'],'adaptive')
            self.assertEqual(detail['workers'],6)
            self.assertGreater(detail['input_chars'],0)
            self.assertGreater(detail['output_chars'],0)

    def test_six_workers_actually_run_concurrently(self):
        barrier=threading.Barrier(6)
        def concurrent(prompt,schema):
            if prompt.startswith('ROLE: baseline_analysis'):
                barrier.wait(timeout=3)
            return self.model(prompt,schema)
        self.items=self.items[:6]
        s=self.service(concurrent)
        r=self.done(s,s.start({'batch_size':1,'workers':6})['id'])
        self.assertEqual(r['settings']['workers'],6)
        self.assertEqual(r['metrics']['verified'],6,r)

    def test_bad_item_retried_once_without_repeating_accepted_items(self):
        self.bad_ids={'https://example.com/1'};self.reject_ids={'https://example.com/2'}
        s=self.service();r=self.done(s,s.start({'batch_size':3})['id'])
        self.assertEqual(r['status'],'requires_review');self.assertEqual(r['metrics']['verified'],5)
        self.assertEqual(r['metrics']['failed'],1);self.assertEqual(r['metrics']['needs_review'],1)
        analyzed=[d['document_id'] for p in self.calls if p.startswith('ROLE: baseline_analysis') for d in json.loads(p.split('DATA:\n')[1])]
        self.assertEqual(analyzed.count('https://example.com/1'),2);self.assertEqual(analyzed.count('https://example.com/0'),1)

    def test_changed_content_does_not_reuse_old_result(self):
        s=self.service();self.done(s,s.start()['id']);self.items[0]['text']='정부 소버린 AI 투자 취소'
        r=self.done(s,s.start()['id']);self.assertEqual(r['metrics']['reused_verified'],6)

    def test_pause_finishes_current_batch_then_resumes(self):
        entered=threading.Event();release=threading.Event()
        def blocking(p,s):
            entered.set();release.wait(3);return self.model(p,s)
        s=self.service(blocking);r=s.start({'batch_size':2,'workers':1});self.assertTrue(entered.wait(3))
        s.pause(r['id']);release.set();paused=self.done(s,r['id'])
        with s.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM bulk_baseline_events WHERE run_id=? AND stage='user_pause_requested'",(r['id'],)).fetchone()[0],1)
        self.assertEqual(paused['status'],'paused');self.assertEqual(paused['metrics']['verified'],2)
        done=self.done(s,s.resume(r['id'])['id']);self.assertEqual(done['metrics']['verified'],7)

    def test_unquoted_word_fragment_is_rejected(self):
        def fragment(p,s):
            result=self.model(p,s)
            for d in result.get('documents',[]):d['keywords']=[{'label':'소버린 AI','source_quote':'소버'}]
            return result
        s=self.service(fragment);r=self.done(s,s.start()['id'])
        self.assertEqual(r['status'],'requires_review');self.assertEqual(r['metrics']['verified'],0)

    def test_empty_sources_remain_in_total_as_explicit_failures(self):
        self.items.append({'source_url':'https://example.com/empty','title':'','text':''})
        s=self.service();r=self.done(s,s.start()['id'])
        self.assertEqual(r['metrics']['total'],8)
        self.assertEqual(r['metrics']['verified'],7)
        self.assertEqual(r['metrics']['failed'],1)
        self.assertEqual(r['coverage']['empty_evidence'],1)
        self.assertEqual(r['status'],'requires_review')

    def test_explicit_resume_retries_only_previously_unverified_documents(self):
        self.bad_ids={'https://example.com/1'}
        s=self.service();r=self.done(s,s.start()['id'])
        self.assertEqual(r['metrics']['verified'],6)
        self.bad_ids.clear();before=len(self.calls)
        done=self.done(s,s.resume(r['id'])['id'])
        self.assertEqual(done['status'],'complete')
        selected=[d['document_id'] for p in self.calls[before:] if p.startswith('ROLE: baseline_analysis') for d in json.loads(p.split('DATA:\n')[1])]
        self.assertEqual(selected,['https://example.com/1'])
        retried=[d for p in self.calls[before:] if p.startswith('ROLE: baseline_analysis') for d in json.loads(p.split('DATA:\n')[1])]
        self.assertTrue(retried[0]['previous_review']['issues'])

    def test_review_feedback_reaches_the_automatic_retry(self):
        self.reject_ids={'https://example.com/1'}
        s=self.service();self.done(s,s.start()['id'])
        attempts=[d for p in self.calls if p.startswith('ROLE: baseline_analysis') for d in json.loads(p.split('DATA:\n')[1]) if d['document_id']=='https://example.com/1']
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[1]['previous_review']['issues'],['과장'])
        self.assertIn('summary',attempts[1]['previous_review']['previous_analysis'])

    def test_cached_source_context_is_included_without_fetching(self):
        item=dict(self.items[0],source_context={'status':'fetched','text':'공식 원문 추가 근거'})
        snapshot=freeze_item(item)
        self.assertEqual(snapshot['source_scope'],'telegram_and_cached_url_excerpt')
        self.assertEqual(snapshot['evidence'][1]['text'],'공식 원문 추가 근거')

if __name__=='__main__':unittest.main()


def test_explicit_single_document_repair_keeps_prior_attempts_and_review(monkeypatch):
    import repair_baseline
    from bulk_baseline import unpack_json
    case=BaselineTests();case.setUp()
    try:
        case.reject_ids.add('https://example.com/0')
        service=case.service()
        run=case.done(service,service.start({'batch_size':1,'workers':1})['id'])
        assert run['status']=='requires_review'
        with service.db() as db:
            rejected=db.execute("SELECT * FROM bulk_baseline_documents WHERE run_id=? AND status='needs_review'",(run['id'],)).fetchone()
            assert rejected['attempts']==2
            identity=rejected['document_id']
        case.reject_ids.clear()
        def unavailable(*args): raise RuntimeError('[engine:capacity]')
        monkeypatch.setattr(repair_baseline,'BulkBaselineService',lambda *a,**k:case.service(model=unavailable))
        monkeypatch.setattr(repair_baseline,'all_corpus_items',lambda db:case.items)
        repair_baseline.repair(case.path,run['id'],identity,'Engine pause must preserve review history')
        with service.db() as db:
            assert db.execute('SELECT status FROM bulk_baseline_runs WHERE id=?',(run['id'],)).fetchone()[0]=='paused'
            assert db.execute('SELECT attempts FROM bulk_baseline_documents WHERE run_id=? AND document_id=?',(run['id'],identity)).fetchone()[0]==2
        monkeypatch.setattr(repair_baseline,'BulkBaselineService',lambda *a,**k:case.service())
        monkeypatch.setattr(repair_baseline,'all_corpus_items',lambda db:case.items)
        result=repair_baseline.repair(case.path,run['id'],identity,'Correct the specific rejected claim')
        assert result['counts']=={'verified':7}
        with service.db() as db:
            row=db.execute('SELECT * FROM bulk_baseline_documents WHERE run_id=? AND document_id=?',(run['id'],identity)).fetchone()
            assert row['attempts']==3
            prior=json.loads(db.execute("SELECT detail FROM bulk_baseline_events WHERE stage='explicit_document_repair'").fetchone()[0])['prior']
            assert prior['attempts']==2 and prior['status']=='needs_review'
            assert not unpack_json(db,prior['result_json'])['verification']['accepted']
            assert unpack_json(db,row['result_json'])['verification']['accepted']
    finally:case.tearDown()
