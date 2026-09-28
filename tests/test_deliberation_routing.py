import unittest
from deliberation import build_deliberation,audit_schema,validate_checks
from review_routing import route_review
from strategic_workflow import AUDIT,evidence_schema

class DeliberationTests(unittest.TestCase):
    def fixture(self):
        evidence=[{'id':'e','text':'국가 기술 소식','url':'https://example.com','title':'기술'}]
        claim=lambda detail:{'title':'관점','detail':detail,'category':'watch_signal','uncertainty':'추가확인','evidence_ids':['e']}
        return evidence,build_deliberation({'national':{'claims':[claim('국가적 기회')]},'technology':{'claims':[claim('기술 비용 우려')]}},evidence)
    def test_actual_positions_not_false_consensus_and_material_omission_rejected(self):
        evidence,result=self.fixture();self.assertEqual(len(result['positions']),2)
        self.assertEqual(result['questions'][0]['status'],'potential_difference')
        check={'difference_id':result['questions'][0]['id'],'material':True,'classification':'contradiction','preserved':False,'reason':'중요차이누락','opposing_evidence_ids':['e'],'change_conditions':['제안: 비용 자료가 확인되면 재판단']}
        self.assertTrue(validate_checks({'deliberation_checks':[check]},result,evidence))
        check['preserved']=True;self.assertEqual(validate_checks({'deliberation_checks':[check]},result,evidence),[])
        schema=evidence_schema(audit_schema(AUDIT,result),evidence)
        self.assertEqual(schema['properties']['deliberation_checks']['items']['properties']['opposing_evidence_ids']['items']['enum'],['e'])
    def test_duplicate_evidence_questions_merge(self):
        evidence,result=self.fixture();reports={p['role']:{'claims':[{'title':p['title'],'detail':p['position'],'category':p['category'],'uncertainty':p['uncertainty'],'evidence_ids':['e','e2']}]} for p in result['positions']}
        combined=build_deliberation(reports,evidence+[{'id':'e2','text':'same'}]);self.assertEqual(len(combined['questions']),1)
        self.assertEqual(combined['questions'][0]['evidence_ids'],['e','e2'])
    def test_routing_preserves_actual_issues_and_bounds(self):
        run={'id':'r','error':'실행 시간이 초과됨','results':{'evidence':[{'id':'e','url':'https://example.com','title':'기술'}],
             'verification':{'issues':['e 인용 누락','기사 날짜 미확인','국가 귀속 과장','문맥 불일치']},'coverage':{'failed_urls':['https://example.com']}}}
        tasks=route_review(run);kinds={task['kind'] for task in tasks}
        self.assertTrue({'source_fetch','source_context','date_unknown','citation_missing','overstatement','execution'}<=kinds)
        self.assertTrue(all(task['max_attempts']==1 for task in tasks))
        self.assertEqual(tasks,route_review(run))
