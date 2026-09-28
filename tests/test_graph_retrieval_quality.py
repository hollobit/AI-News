import copy
import unittest
from unittest.mock import patch

from graph_rag import GraphResult, build_retrieval, answer_question, review_answer
from evidence_search import LexicalIndex


def graph():
    evidence=[
        {'id':'sovereign','document_id':'doc1','title':'Sovereign AI procurement','text':'Sovereign AI gives data control but increases infrastructure costs.','day':'2026-09-10'},
        {'id':'copy','document_id':'doc1','title':'Sovereign AI repost','text':'Sovereign AI gives data control but increases infrastructure costs.','day':'2026-09-11'},
        {'id':'noise','document_id':'doc2','title':'이메일 데이터 주권과 비용 절감','text':'이메일 데이터 비용을 줄였다.','day':'2026-09-12'},
        {'id':'china','document_id':'doc3','title':'중국의 반도체 수출통제','text':'중국은 반도체 수출통제를 검토한다.','day':'2026-09-10'},
        {'id':'us','document_id':'doc4','title':'미국 반도체 수출통제','text':'미국은 반도체 수출통제 정책을 변경했다.','day':'2026-09-12'},
    ]
    nodes=[{'id':e['id'],'name':e['title'],'summary':e['text'],'type':'Concept',
            'evidence_ids':[e['id']],'support_count':1000 if e['id']=='noise' else 1} for e in evidence]
    return GraphResult({'nodes':nodes,'edges':[],'evidence':evidence})


class RetrievalQualityTests(unittest.TestCase):
    def test_changed_source_excludes_whole_claim_but_preserves_independent_claim(self):
        from graph_rag import current_workflow_graph
        value={'nodes':[
            {'id':'bad','type':'StrategicClaim','evidence_ids':['e1','e2']},
            {'id':'good','type':'StrategicClaim','evidence_ids':['e2']},
            {'id':'source1','type':'SourceDocument','evidence_ids':['e1']},
            {'id':'source2','type':'SourceDocument','evidence_ids':['e2']}],
            'edges':[{'source':'bad','target':'source1','evidence_ids':['e1']},
                     {'source':'bad','target':'source2','evidence_ids':['e2']},
                     {'source':'good','target':'source2','evidence_ids':['e2']}],
            'evidence':[{'id':'e1','origin':'fetched_url_excerpt','url':'https://example.com/1','text':'old'},
                        {'id':'e2','origin':'fetched_url_excerpt','url':'https://example.com/2','text':'current'}]}
        original=copy.deepcopy(value)
        result,count=current_workflow_graph(value,{'https://example.com/1':{'status':'fetched','text':'changed'},
            'https://example.com/2':{'status':'fetched','text':'current'}})
        self.assertEqual(count,1)
        self.assertEqual({n['id'] for n in result['nodes']},{'good','source2'})
        self.assertEqual(len(result['edges']),1)
        self.assertEqual(value,original)

    def test_korean_particles_and_english_alias_anchor_subject(self):
        result=build_retrieval(graph(),'소버린AI의 도입에서 데이터 주권과 비용은 무엇인가?')
        self.assertFalse(result['no_hits'])
        self.assertEqual(len(result['evidence']),1)
        self.assertEqual(result['evidence'][0]['document_id'],'doc1')
        self.assertNotIn('noise',[n['id'] for n in result['nodes']])

    def test_comparison_preserves_both_sides_and_avoids_duplicate_documents(self):
        result=build_retrieval(graph(),'미국과 중국의 반도체 수출통제를 비교해주세요')
        self.assertEqual({e['id'] for e in result['evidence']},{'china','us'})

    def test_graph_interpretation_cannot_displace_direct_source_concept(self):
        value=graph()
        value['nodes'][2]['name']='소버린 AI 비용'
        value['nodes'][2]['summary']='소버린 AI 도입 비용'
        result=build_retrieval(value,'소버린 AI 비용')
        self.assertNotIn('noise',[e['id'] for e in result['evidence']])

    def test_evidence_only_query_can_find_node_with_unrelated_label(self):
        value=graph();value['nodes'][0]['name']='자료';value['nodes'][0]['summary']=''
        result=build_retrieval(value,'Sovereign AI procurement')
        self.assertEqual(result['evidence'][0]['document_id'],'doc1')

    def test_selected_node_limits_evidence_and_returns_isolated_objects(self):
        value=graph();result=build_retrieval(value,'수출통제',['china'])
        self.assertEqual([e['id'] for e in result['evidence']],['china'])
        result['evidence'][0]['text']='changed'
        self.assertNotEqual(value['evidence'][3]['text'],'changed')

    def test_no_relevant_technical_concept_does_not_fall_back_to_popular_generic_hub(self):
        value=graph()
        self.assertTrue(build_retrieval(value,'월드 모델 데이터 비용은?')['no_hits'])

    def test_answer_cache_is_content_bound_and_returns_isolated_values(self):
        value=graph();value.search_key=('test-answer-cache',)
        response={'answer':'통제와 비용을 비교해야 합니다.','claims':[{'text':'자료 통제권과 인프라 비용을 함께 검토한다.','evidence_ids':['sovereign']}],'limitations':['발췌 기준']}
        # Give the sole current copy the deterministic highest rank.
        value['evidence']=value.full_evidence=[e for e in value['evidence'] if e['id']!='copy']
        audit={'checks':[{'claim_index':0,'supported':True,'reason':'원문 확인'}],
               'answer_supported':True,'missing_information':[]}
        def generate(prompt,schema,**kwargs):
            return copy.deepcopy(audit if 'checks' in schema['properties'] else response)
        with patch('semantic.run_structured',side_effect=generate) as run:
            first=answer_question(value,'소버린AI 비용')
            first['claims'][0]['text']='caller mutation'
            second=answer_question(value,'소버린AI 비용')
            self.assertTrue(second['timing']['answer_cache_hit'])
            self.assertNotEqual(second['claims'][0]['text'],'caller mutation')
            self.assertEqual(run.call_count,2)
            value.search_key=('test-answer-cache','new-revision')
            value.full_evidence[0]['text']+=' Source changed.'
            answer_question(value,'소버린AI 비용')
            self.assertEqual(run.call_count,4)

    def test_semantic_review_removes_unsupported_claim_and_rebuilds_summary(self):
        response={'answer':'비용이 들지만 20% 절감된다.','claims':[
            {'text':'인프라 비용이 증가한다.','evidence_ids':['sovereign']},
            {'text':'비용이 20% 절감된다.','evidence_ids':['sovereign']}],'limitations':[]}
        audit={'checks':[{'claim_index':1,'supported':False,'reason':'수치 없음'},
                         {'claim_index':0,'supported':True,'reason':'원문 확인'}],
               'answer_supported':False,'missing_information':['절감률은 확인할 수 없습니다.']}
        with patch('semantic.run_structured',return_value=audit) as run:
            result=review_answer(response,{'question':'소버린AI 비용','evidence':graph()['evidence']})
        self.assertIn('인프라 비용이 증가한다.',result['answer'])
        self.assertTrue(result['answer'].startswith('현재 확보한 근거로는'))
        self.assertNotIn('20%',result['answer'])
        self.assertEqual(len(result['claims']),1)
        self.assertEqual(result['verification']['removed_unsupported_claims'],1)
        self.assertIn('절감률은 확인할 수 없습니다.',result['limitations'])
        self.assertNotIn('이메일 데이터',run.call_args.args[0])

    def test_incomplete_audit_is_rejected_and_not_cached(self):
        value=graph();value.search_key=('invalid-audit-not-cached',)
        value['evidence']=value.full_evidence=[e for e in value['evidence'] if e['id']!='copy']
        response={'answer':'비용이 증가한다.','claims':[{'text':'인프라 비용이 증가한다.',
                  'evidence_ids':['sovereign']}],'limitations':[]}
        invalid={'checks':[],'answer_supported':True,'missing_information':[]}
        with patch('semantic.run_structured',side_effect=[copy.deepcopy(response),invalid,
                                                         copy.deepcopy(response),invalid]) as run:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,'검토가 완결되지'):
                    answer_question(value,'소버린AI 비용 부담을 설명해주세요')
            self.assertEqual(run.call_count,4)

    def test_all_unsupported_claims_produce_explicit_insufficient_evidence(self):
        response={'answer':'보장된다.','claims':[{'text':'보장된다.','evidence_ids':['sovereign']}],
                  'limitations':[]}
        audit={'checks':[{'claim_index':0,'supported':False,'reason':'보장 없음'}],
               'answer_supported':False,'missing_information':['보장 여부의 근거가 없습니다.']}
        with patch('semantic.run_structured',return_value=audit):
            result=review_answer(response,{'question':'보장?','evidence':graph()['evidence']})
        self.assertEqual(result['claims'],[])
        self.assertIn('확인할 수 없습니다',result['answer'])
