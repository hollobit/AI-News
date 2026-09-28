import copy
import unittest
from event_observations import report_schema,validate_events,audit_schema,events_audit_issues
from strategic_workflow import REPORT,AUDIT,evidence_schema,WorkflowService,digest

class EventTests(unittest.TestCase):
    def setUp(self):
        self.evidence=[{'id':'e','url':'https://example.org/news','text':'미국 정부는 정책 초안을 발표했다.'}]
        self.event={'actor':'미국 정부','action':'정책 초안 발표','target':'','outcome':'','occurred_at':'','actor_countries':['미국'],'affected_countries':[],'evidence_ids':['e'],'uncertainty':'이행 여부 미확인'}
    def test_schema_isolated_and_unknown_fields_remain_unknown(self):
        before=copy.deepcopy(REPORT);schema=evidence_schema(report_schema(REPORT,self.evidence),self.evidence)
        self.assertEqual(REPORT,before)
        self.assertEqual(schema['properties']['event_observations']['items']['properties']['evidence_ids']['items']['enum'],['e'])
        self.assertEqual(validate_events([self.event],self.evidence)[0]['occurred_at'],'')
        self.assertEqual(validate_events([],self.evidence),[])
    def test_empty_action_fake_citation_and_duplicate_document_rejected(self):
        for change in ({'action':''},{'evidence_ids':[]},{'evidence_ids':['fake']}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_events([dict(self.event,**change)],self.evidence)
        with self.assertRaises(ValueError):validate_events([self.event,self.event],self.evidence)

    def test_url_variants_share_one_document_without_merging_distinct_articles(self):
        evidence=self.evidence+[{'id':'f','url':'https://example.org/news?utm_source=test','text':'추가 발췌'}]
        event=dict(self.event,evidence_ids=['e','f'])
        self.assertEqual(validate_events([event],evidence),[event])
        with self.assertRaises(ValueError):validate_events([self.event,dict(self.event,evidence_ids=['f'])],evidence)
        evidence[1]['url']='https://example.org/different'
        with self.assertRaises(ValueError):validate_events([event],evidence)
    def test_every_event_field_review_and_reference_required(self):
        self.assertTrue(events_audit_issues({'checked_evidence_ids':['e']},[self.event],self.evidence))
        self.assertTrue(events_audit_issues({'checked_event_indices':[0],'checked_evidence_ids':[]},[self.event],self.evidence))
        audit={'checked_event_indices':[0],'checked_evidence_ids':['e']}
        self.assertEqual(events_audit_issues(audit,[self.event],self.evidence),[])
        self.assertEqual(events_audit_issues({'checked_event_indices':[],'checked_evidence_ids':[]},[],self.evidence),[])
    def test_cached_audit_cannot_reuse_changed_event(self):
        report={'event_observations':[self.event]}
        audit={'accepted':True,'issues':[],'checked_event_indices':[0],'checked_evidence_ids':['e'],'event_hash':digest([self.event]),'report_hash':digest(report),'evidence_hash':digest(self.evidence)}
        service=object.__new__(WorkflowService)
        self.assertTrue(service._current_audit(audit,self.evidence,report)['accepted'])
        changed={'event_observations':[dict(self.event,actor='다른 기관')]}
        audit['report_hash']=digest(changed)
        self.assertFalse(service._current_audit(audit,self.evidence,changed)['accepted'])

    def test_evidence_enum_never_constrains_audit_issues_or_limitations(self):
        schema=evidence_schema(AUDIT,self.evidence)
        self.assertEqual(schema['properties']['checked_evidence_ids']['items']['enum'],['e'])
        self.assertNotIn('enum',schema['properties']['issues']['items'])
        self.assertNotIn('enum',schema['properties']['limitations']['items'])
        report=evidence_schema(REPORT,self.evidence)
        self.assertNotIn('enum',report['properties']['limitations']['items'])

    def test_risk_enum_leaves_observations_assets_and_mitigations_as_text(self):
        from risk_analysis import RISK_SCHEMA
        before=copy.deepcopy(RISK_SCHEMA)
        schema=evidence_schema(RISK_SCHEMA,self.evidence)
        risk=schema['properties']['risks']['items']['properties']
        for name in ('affected_assets','affected_actors','observed_indicators','assumptions','escalation_signals','mitigations','counter_evidence'):
            with self.subTest(field=name):self.assertNotIn('enum',risk[name]['items'])
        for name in ('assessed_evidence_ids','not_assessable_evidence_ids'):
            self.assertEqual(schema['properties'][name]['items']['enum'],['e'])
        self.assertEqual(risk['evidence_ids']['items']['enum'],['e'])
        self.assertEqual(RISK_SCHEMA,before)
