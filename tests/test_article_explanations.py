import json
from article_explanations import map_context,context_hash,generate,SECTIONS,ArticleExplanations,VERSION,comparison_passages
from source_store import save,now
import pytest

URL='https://example.com/news'
def view(current=3):
    return {'days':['2026-09-%02d'%i for i in range(1,15)],'evidence':{'a':{'url':URL}},'nodes':[{'id':'topic:a','label':'AI','current':current,'previous':0,'series':[0]*11+[1,1,1],'count':3,'evidence_by_day':[['a']]},{'id':'keyword:b','label':'반도체','current':2,'previous':4,'series':[0]*14,'count':5,'evidence_by_day':[]}],'edges':[{'id':'edge:a','source':'topic:a','target':'keyword:b','current':2,'previous':0,'count':2,'series':[0]*14,'relation':'공동 관측'}],'computed_at':'today','method':'고유 문서의 공동 관측'}

def test_relations_changes_and_missing_sample():
    c=map_context(view(),URL)
    assert c['direct_node_ids']==['topic:a']
    assert len(c['nodes'])==2 and len(c['edges'])==1
    assert c['changes'][0]['percent'] is None
    assert c['changes'][2]['sparse']
    assert c['changes'][1]['delta']==-2
    assert context_hash(c)!=context_hash(map_context(view(4),URL))
    assert not map_context(view(),'https://example.com/missing')['nodes']

def test_independent_review_all_sections_and_bad_citation():
    result={k:[{'text':'근거에 따른 설명','kind':'interpretation','evidence_ids':['article:0']}] for k in SECTIONS}
    calls=[]
    def runner(prompt,schema,**kwargs):
        calls.append(prompt)
        return result if len(calls)==1 else {'checks':[{'index':i,'supported':i!=1,'useful':True,'reason':'확인'} for i in range(len(SECTIONS))]}
    sections,audit=generate({'evidence':[{'id':'article:0','text':'본문'}],'observation':{}},runner,repair=False)
    assert 'network_changes' in sections and not audit['passed']
    assert len(calls)==2
    result['facts'][0]['evidence_ids']=['invented']
    with pytest.raises(RuntimeError,match='인용'):generate({'evidence':[{'id':'article:0'}]},lambda *a,**k:result)

def test_missing_review_rejected():
    result={k:[{'text':'설명','kind':'limitation','evidence_ids':['a']}] for k in SECTIONS}
    def runner(prompt,schema,**kwargs):return {'checks':[]} if kwargs['role'].endswith('review') else result
    with pytest.raises(RuntimeError,match='누락'):generate({'evidence':[{'id':'a'}],'observation':{}},runner)

def test_source_and_observation_invalidation(tmp_path):
    class Observatory:
        data=view()
        def request(self,w):return self.data
    obs=Observatory();service=ArticleExplanations(tmp_path/'db',obs)
    raw={'status':'fetched','title':'AI','text':'본문'*100,'evidence_scope':'full'}
    with service.db() as db:
        save(db,URL,raw,now());h=db.execute('SELECT hash FROM source_health').fetchone()[0]
        output={'version':VERSION,'sources':[{'url':URL,'hash':h}],'context_hash':context_hash(map_context(obs.data,URL))}
        db.execute('INSERT INTO article_explanations VALUES (?,?,?,?,?,?,?)',('one',URL,'complete',json.dumps(output),'',now(),1))
    assert service.get(URL)['status']=='complete'
    obs.data=view(4)
    assert service.get(URL)['status']=='stale' and service.get(URL)['result'] is None
    obs.data=view()
    with service.db() as db:save(db,URL,dict(raw,status='failed',error='429'),now())
    assert service.get(URL)['status']=='stale'
    service.close()

def test_exact_membership_not_limited_by_display_samples():
    v=view();v['evidence']={};v['article_nodes']={URL:['topic:a']}
    context=map_context(v,URL)
    assert context['direct_node_ids']==['topic:a'] and len(context['edges'])==1


def test_worker_stores_reviewed_output_and_reuses_url(tmp_path):
    class Observatory:
        def request(self,w):return view()
    captured=[]
    def generator(bundle):
        captured.append(bundle)
        return {k:[] for k in SECTIONS},{'passed':True,'method':'test_review'}
    service=ArticleExplanations(tmp_path/'db',Observatory(),reader=lambda url:{'url':url,'status':'fetched','title':'AI chip news','text':'AI chip article evidence. '*200,'evidence_scope':'public_web','reader':'fixture'},generator=generator)
    with service.db() as db:db.execute('INSERT INTO article_explanations VALUES (?,?,?,?,?,?,?)',('one',URL,'queued',None,'',now(),1))
    service._run(URL)
    output=service.get(URL)
    assert output['status']=='complete',output
    assert captured[0]['observation']['changes']
    assert any(e['source_kind']=='article_passage' for e in captured[0]['evidence'])
    assert service.submit(URL)['id']=='one'
    assert len(captured)==1
    service.close()


def test_optional_sections_empty_and_editor_removes_filler():
    result={k:[] for k in SECTIONS}
    for k in ('summary','facts','meaning','signals'):
        result[k]=[{'text':'설명','kind':'interpretation','evidence_ids':['a']}]
    def runner(prompt,schema,**kwargs):
        if kwargs['role'].endswith('review'):
            return {'checks':[{'index':i,'supported':True,'useful':i!=3,'reason':'후속 관찰 일반론' if i==3 else '유효'} for i in range(4)]}
        assert schema['properties']['uncertainty']['minItems']==0
        return result
    output,audit=generate({'evidence':[{'id':'a'}],'observation':{}},runner)
    assert audit['passed'] and audit['removed_items']==1
    assert output['signals']==[] and output['uncertainty']==[]
    assert output['meaning']


def test_recommendation_and_duplicate_passages_not_comparisons():
    candidates=[{'source_url':'https://example.com/other','title':'Other story','text':'Recommended: AI launches product. Read more'},
                {'source_url':'https://example.com/repost','title':'AI launches product','text':'Repost'},
                {'source_url':'https://example.com/prior','title':'Previous product','text':'Earlier version requires Linux.'}]
    selected=comparison_passages(candidates,URL,'AI launches product','Full body')
    assert [e['source_url'] for e in selected]==['https://example.com/prior']


def test_failed_core_is_revised_once_and_reviewed_again():
    calls=[]
    draft={k:[] for k in SECTIONS}
    for k in ('summary','facts','meaning'):draft[k]=[{'text':'구체적인 설명','kind':'fact','evidence_ids':['a']}]
    def runner(prompt,schema,**kwargs):
        calls.append(kwargs['role'])
        if not kwargs['role'].endswith('review'):return draft
        return {'checks':[{'index':i,'supported':len(calls)==4 or i!=0,'useful':True,'reason':'주체 확인'} for i in range(3)]}
    output,audit=generate({'evidence':[{'id':'a'}],'observation':{}},runner)
    assert audit['passed'] and audit['revision_attempts']==1
    assert len(calls)==4 and output['summary']
