import math
import unittest
from strategic_assessments import score_document, profile_value, policy_observations


class AssessmentTests(unittest.TestCase):
    def test_unknown_risk_and_opportunity_are_not_zero(self):
        value=score_document({'id':'d','title':'정부 국가안보 수출통제','text':''},[],[])
        self.assertIsNone(value['scores']['risk'])
        self.assertIsNone(value['scores']['opportunity'])
        self.assertIsNone(value['priority']['score'])
        self.assertEqual([0,100],value['score_details']['risk']['range'])

    def test_government_priority_does_not_change_evidence_score(self):
        plain=score_document({'id':'d','title':'새 소식','text':''},[],[])
        government=score_document({'id':'d','title':'정부 국가안보 수출통제','text':''},[],[])
        self.assertGreater(government['scores']['importance'],plain['scores']['importance'])
        self.assertEqual(plain['scores']['evidence'],government['scores']['evidence'])
        self.assertIsNone(government['scores']['risk'])

    def test_current_reviewed_opportunity_and_stale_excluded(self):
        claim={'id':'c','status':'current_reviewed','category':'opportunity'}
        current=score_document({'id':'d','title':'AI 기회'},[claim],[])
        self.assertEqual(45,current['score_details']['evidence']['checks']['current_reviewed_claim'])
        self.assertIsNotNone(current['scores']['opportunity'])
        claim['status']='needs_review'
        self.assertIsNone(score_document({'id':'d'},[claim],[])['scores']['opportunity'])

    def test_nonfinite_or_boolean_weights_rejected(self):
        for value in [math.inf,-math.inf,math.nan,True,-1]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                profile_value({'name':'invalid','weights':dict(importance=value,risk=1,evidence=1,opportunity=1)})

    def test_negated_policy_is_not_implementation_confirmation(self):
        found=policy_observations({'id':'d','text':'정부는 규제를 시행하지 않는다.'})
        self.assertTrue(found)
        self.assertTrue(all(row['status']=='conditional_or_negated_mention' for row in found))
