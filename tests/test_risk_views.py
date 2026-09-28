import copy
import json
import sqlite3
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from test_risk_analysis import fixture
from risk_analysis import _digest,read_risks
from risk_views import priority_score,read_risk_page,read_risk_detail
from risk_source_state import evidence_index_key

NOW=datetime(2026,9,15,tzinfo=timezone.utc)

class RiskViewTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT,updated_at TEXT)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT)')
    def tearDown(self):self.db.close()
    def save(self,number,day='2026-09-10',same=False):
        payload=fixture(day)
        if not same:payload['risk_report']['risks'][0]['title']='접근 통제 실패 '+str(number)
        payload['risk_verification']['report_hash']=_digest(payload['risk_report'])
        self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?)',(str(number),'complete','','2026-09-15'))
        self.db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(str(number),'final',json.dumps(payload)))
    def current(self,db,evidence):return {evidence_index_key(e):{'status':'matched_current','reason':'exact match','source_dates':[e.get('published_at')] if e.get('published_at') else []} for e in evidence}

    def test_explainable_score_and_unknown_ranges(self):
        risk={'current_severity':'high','future_likelihood':'moderate','impact_domains':['security','industry']}
        value=priority_score(risk)
        self.assertEqual(value['score'],68)
        self.assertEqual(value['current_score'],75)
        self.assertEqual(value['future_attention_score'],50)
        self.assertIsNone(priority_score(dict(risk,source_status='changed'))['score'])
        self.assertIsNone(priority_score(dict(risk,date_status='stale'))['current_score'])
        risk['future_likelihood']='unknown'
        partial=priority_score(risk)
        self.assertIsNone(partial['score']);self.assertEqual(partial['range'],[55.5,80.5])
        risk['current_severity']='unknown'
        self.assertEqual(priority_score(risk)['status'],'unknown')
        self.assertIsNone(priority_score(risk)['score'])
        self.assertEqual(priority_score(dict(risk,impact_domains=[]))['range'],[0,100])

    def test_pagination_crosses_legacy_200_limit(self):
        for index in range(205):self.save(index)
        self.assertEqual(len(read_risks(self.db,current_time=NOW)['risks']),200)
        with patch('risk_source_state.evidence_source_states',self.current):
            page=read_risk_page(self.db,{'page':['18'],'page_size':['12']},current_time=NOW)
            self.assertEqual(page['total'],205)
            self.assertEqual(page['pagination']['total_pages'],18)
            self.assertEqual(len(page['items']),1)
            self.assertNotIn('evidence',page['items'][0])
            detail=read_risk_detail(self.db,page['items'][0]['id'],current_time=NOW)
            self.assertEqual(len(detail['risk']['evidence']),1)
            self.assertNotIn('raw_evidence',detail['risk'])

    def test_source_change_invalidates_current_and_future_numeric_priority(self):
        self.save(1)
        def changed(db,evidence):return {evidence_index_key(e):{'status':'changed','reason':'updated source'} for e in evidence}
        with patch('risk_source_state.evidence_source_states',changed):
            risk=read_risk_page(self.db,current_time=NOW)['items'][0]
        self.assertEqual(risk['current_severity'],'unknown')
        self.assertEqual(risk['assessed_current_severity'],'high')
        self.assertEqual(risk['future_likelihood'],'unknown')
        self.assertEqual(risk['status'],'needs_review')
        self.assertIsNone(risk['priority']['score'])
        self.assertIsNone(risk['priority']['current_score'])

    def test_stale_does_not_borrow_old_current_rating(self):
        self.save(1,'2025-01-01')
        with patch('risk_source_state.evidence_source_states',self.current):risk=read_risk_page(self.db,current_time=NOW)['items'][0]
        self.assertIsNone(risk['priority']['current_score'])
        self.assertIsNone(risk['priority']['score'])
        self.assertEqual(risk['status'],'needs_review')

    def test_telegram_timestamp_does_not_supply_article_recency(self):
        self.save(1)
        def no_article_date(db,evidence):return {evidence_index_key(e):{'status':'matched_current','source_dates':[]} for e in evidence}
        with patch('risk_source_state.evidence_source_states',no_article_date):
            risk=read_risk_page(self.db,current_time=NOW)['items'][0]
        self.assertEqual(risk['date_status'],'unknown')
        self.assertEqual(risk['evidence_latest_date'],'')
        self.assertIsNone(risk['priority']['current_score'])

    def test_exact_duplicates_keep_history_and_detail_alias(self):
        self.save(1,same=True);self.save(2,same=True)
        with patch('risk_source_state.evidence_source_states',self.current):
            page=read_risk_page(self.db,current_time=NOW)
            self.assertEqual(page['total'],1)
            self.assertEqual(page['coverage']['duplicate_records'],1)
            detail=read_risk_detail(self.db,page['items'][0]['id'],current_time=NOW)['risk']
            self.assertEqual(len(detail['run_history']),2)
            alias=detail['run_history'][1]['id']
            self.assertEqual(read_risk_detail(self.db,alias,current_time=NOW)['risk']['id'],detail['id'])
            self.assertIsNone(read_risk_detail(self.db,'not-found',current_time=NOW))

    def test_pending_workflows_have_independent_complete_pagination(self):
        for index in range(25):
            self.save(index)
        self.db.execute("UPDATE strategic_workflow_runs SET status='needs_review'")
        with patch('risk_source_state.evidence_source_states',self.current):
            page=read_risk_page(self.db,{'status':['all'],'pending_page':['3']},current_time=NOW)
        self.assertEqual(page['total'],0)
        self.assertEqual(page['review_pagination']['total'],25)
        self.assertEqual(page['review_pagination']['total_pages'],3)
        self.assertEqual(len(page['needs_review']),1)

    def test_filters_and_page_size_bounds(self):
        self.save(1)
        with patch('risk_source_state.evidence_source_states',self.current):
            self.assertEqual(read_risk_page(self.db,{'severity':['low']},current_time=NOW)['total'],0)
            self.assertEqual(read_risk_page(self.db,{'page_size':['500']},current_time=NOW)['pagination']['page_size'],48)
            with self.assertRaises(ValueError):read_risk_page(self.db,{'severity':['certain']},current_time=NOW)

    def resolution(self,risk_id,**fields):
        self.db.execute('CREATE TABLE IF NOT EXISTS risk_review_resolutions(risk_id TEXT,status TEXT,reason TEXT,corrected_source_url TEXT,replacement_workflow_run_id TEXT,created_at TEXT)')
        record=dict(status='withdrawn_source_mismatch',reason='명시적 원문 연결 오류 확인',corrected_source_url='https://example.com/a',replacement_workflow_run_id='',created_at='2026-09-15T12:00:00Z')
        record.update(fields)
        self.db.execute('INSERT INTO risk_review_resolutions VALUES (?,?,?,?,?,?)',(risk_id,*(record[key] for key in ('status','reason','corrected_source_url','replacement_workflow_run_id','created_at'))))

    def test_explicit_withdrawal_keeps_history_and_original_detail(self):
        self.save(1)
        with patch('risk_source_state.evidence_source_states',self.current):
            old=read_risk_page(self.db,current_time=NOW)['items'][0]
            self.resolution(old['id'])
            active=read_risk_page(self.db,current_time=NOW)
            self.assertEqual(active['total'],0)
            self.assertEqual(active['coverage']['withdrawn'],1)
            self.assertEqual(active['coverage']['resolution_pending'],1)
            history=read_risk_page(self.db,{'status':['history']},current_time=NOW)
            self.assertEqual(history['total'],1)
            detail=read_risk_detail(self.db,old['id'],current_time=NOW)['risk']
            self.assertEqual(detail['lifecycle'],'withdrawn_source_mismatch')
            self.assertEqual(detail['title'],old['title'])
            self.assertEqual(detail['resolution']['verification_status'],'pending_resolution')

    def test_unrecognized_or_unreasoned_resolution_never_hides_risk(self):
        self.save(1)
        with patch('risk_source_state.evidence_source_states',self.current):
            identity=read_risk_page(self.db,current_time=NOW)['items'][0]['id']
            self.resolution(identity,status='resolved')
            self.assertEqual(read_risk_page(self.db,current_time=NOW)['total'],1)
            self.db.execute('DELETE FROM risk_review_resolutions')
            self.resolution(identity,reason='')
            self.assertEqual(read_risk_page(self.db,current_time=NOW)['total'],1)

    def test_replacement_requires_independent_gate_current_source_and_corrected_url(self):
        self.save(1);self.save(2)
        with patch('risk_source_state.evidence_source_states',self.current):
            old=next(x for x in read_risk_page(self.db,current_time=NOW)['items'] if x['workflow_run_id']=='1')
            self.resolution(old['id'],replacement_workflow_run_id='2')
            self.assertEqual(read_risk_page(self.db,{'status':['resolved']},current_time=NOW)['total'],1)
            self.db.execute("UPDATE risk_review_resolutions SET corrected_source_url='https://example.com/wrong'")
            self.assertEqual(read_risk_page(self.db,{'status':['resolved']},current_time=NOW)['total'],0)
            self.db.execute("UPDATE risk_review_resolutions SET corrected_source_url='https://example.com/a'")
            self.db.execute("UPDATE strategic_workflow_runs SET status='needs_review' WHERE id='2'")
            self.assertEqual(read_risk_page(self.db,{'status':['resolved']},current_time=NOW)['total'],0)
            self.db.execute("UPDATE strategic_workflow_runs SET status='complete' WHERE id='2'")
        def changed(db,evidence):return {evidence_index_key(e):{'status':'changed'} for e in evidence}
        with patch('risk_source_state.evidence_source_states',changed):
            self.assertEqual(read_risk_page(self.db,{'status':['resolved']},current_time=NOW)['total'],0)

    def test_context_reassessment_moves_to_history_after_replacement_verified(self):
        self.save(1);self.save(2)
        with patch('risk_source_state.evidence_source_states',self.current):
            old=next(x for x in read_risk_page(self.db,current_time=NOW)['items'] if x['workflow_run_id']=='1')
            self.resolution(old['id'],status='context_reassessment',replacement_workflow_run_id='2')
            page=read_risk_page(self.db,current_time=NOW)
            self.assertEqual(page['total'],1)
            self.assertEqual(page['coverage']['superseded'],1)
            self.assertEqual(page['coverage']['withdrawn'],0)
            self.assertEqual(page['coverage']['history_total'],1)
            self.assertEqual(page['coverage']['resolved'],1)
            self.assertEqual(read_risk_page(self.db,{'status':['history']},current_time=NOW)['total'],1)
            self.assertEqual(read_risk_page(self.db,{'status':['withdrawn']},current_time=NOW)['total'],0)

    def test_superseded_reactivates_when_replacement_invalid_but_withdrawal_remains(self):
        self.save(1);self.save(2)
        def changed(db,evidence):return {evidence_index_key(e):{'status':'changed','reason':'original mismatch'} for e in evidence}
        with patch('risk_source_state.evidence_source_states',changed):
            originals=read_risk_page(self.db,current_time=NOW)['items']
            first=originals[0]['id'];second=originals[1]['id']
            before=read_risk_detail(self.db,first,current_time=NOW)['risk']
            self.resolution(first,status='source_correction',replacement_workflow_run_id='replacement')
            self.resolution(second,status='withdrawn_source_mismatch',replacement_workflow_run_id='replacement')
            with patch('risk_views._replacement_verified',return_value=True):
                page=read_risk_page(self.db,current_time=NOW)
                self.assertEqual(page['total'],0)
                self.assertEqual(page['coverage']['source_review_required'],0)
                self.assertEqual(page['coverage']['historical_source_review_required'],2)
                self.assertEqual(page['coverage']['source_review_archive_total'],2)
                self.assertEqual(page['coverage']['withdrawn'],1)
                self.assertEqual(page['coverage']['superseded'],1)
                old=read_risk_detail(self.db,first,current_time=NOW)['risk']
                self.assertEqual(old['source_checks'],before['source_checks'])
                self.assertEqual(old['priority'],before['priority'])
                self.assertEqual(read_risk_page(self.db,{'status':['superseded']},current_time=NOW)['total'],1)
            with patch('risk_views._replacement_verified',return_value=False):
                page=read_risk_page(self.db,current_time=NOW)
                self.assertEqual(page['total'],1)
                self.assertEqual(page['items'][0]['lifecycle'],'active')
                self.assertEqual(page['coverage']['source_review_required'],1)
                self.assertEqual(page['coverage']['historical_source_review_required'],1)
                self.assertEqual(page['coverage']['superseded'],0)
                self.assertEqual(page['coverage']['withdrawn'],1)
                self.assertEqual(page['coverage']['resolution_pending'],2)

    def test_ui_labels_distinguish_withdrawal_supersession_and_archive_changes(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        html=(root/'static/risks.html').read_text()
        script=(root/'static/risks.js').read_text()
        self.assertIn('value="superseded"',html)
        self.assertIn('value="withdrawn"',html)
        self.assertIn('후속 검증 평가로 대체 · 과거 기록',script)
        self.assertIn('이력에 보존된 원문 변경',script)
        self.assertIn('historical_source_review_required',script)
