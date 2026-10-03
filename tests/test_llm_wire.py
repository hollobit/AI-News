import json
from llm_wire import prepare,restore


def fixture():
    ids=['news_'+'a'*24,'url_'+'b'*24]
    evidence=[dict(id=i,text='원문 사실과 반대 근거 그대로',url='https://example.com?q=1&x=2',article_date='2026-01-02') for i in ids]
    data=dict(evidence=evidence,context={'report':{'claims':[{'evidence_ids':ids,'detail':'모든 조건 보존'}]}})
    schema={'properties':{'evidence_ids':{'items':{'enum':ids}}}}
    return data,schema,ids


def test_transport_roundtrip_keeps_evidence_text_and_original_objects():
    data,schema,ids=fixture();prompt='ROLE: verification\nDATA:\n'+json.dumps(data,ensure_ascii=False)
    packed,wire,aliases=prepare(prompt,schema)
    payload=json.loads(packed.split('DATA:\n')[1])
    assert payload['evidence'][0]['id']=='E1'
    assert payload['evidence'][0]['text']==data['evidence'][0]['text']
    assert payload['evidence'][0]['url']==data['evidence'][0]['url']
    assert payload['context']['report']['claims'][0]['evidence_ids']==['E1','E2']
    assert wire['properties']['evidence_ids']['items']['enum']==['E1','E2']
    assert data['evidence'][0]['id']==ids[0] and schema['properties']['evidence_ids']['items']['enum']==ids
    restored=restore({'evidence_ids':['E1','E2','E99'],'limitations':['E1은 미확인']},aliases)
    assert restored['evidence_ids']==ids+['E99']  # Unknown citations remain invalid.
    assert restored['limitations']==[ids[0]+'은 미확인']
    assert len(packed)<len(prompt)


def test_alias_collision_custom_ids_and_unstructured_prompts_stay_original():
    data,schema,ids=fixture()
    for replacement in ('E1 증거와 비교','E2','미상'):
        data['evidence'][0]['text']=replacement
        if replacement=='미상':data['evidence'][0]['id']='custom'
        prompt='ROLE: x\nDATA:\n'+json.dumps(data)
        assert prepare(prompt,schema)==(prompt,schema,{})
    assert prepare('no JSON',{})==('no JSON',{},{})


def test_report_prose_references_are_mapped_before_review():
    data,schema,ids=fixture()
    data['context']['report']['limitations']=[ids[0]+'만으로는 미확인']
    prompt='ROLE: verification\nDATA:\n'+json.dumps(data,ensure_ascii=False)
    packed,_,aliases=prepare(prompt,schema)
    payload=json.loads(packed.split('DATA:\n')[1])
    assert payload['context']['report']['limitations']==['E1만으로는 미확인']
    assert restore(payload['context']['report']['limitations'],aliases)==data['context']['report']['limitations']
    data['evidence'][0]['text']=ids[0]+'라는 문자 자체를 설명하는 원문'
    prompt='ROLE: verification\nDATA:\n'+json.dumps(data,ensure_ascii=False)
    assert prepare(prompt,schema)==(prompt,schema,{})


def test_bounded_prose_keeps_real_citation_lengths():
    from llm_wire import prepare
    identity='url_'+'a'*24
    prompt='ROLE: risk_patch\nDATA:\n'+json.dumps({'evidence':[{'id':identity,'text':'원문'}], 'report':{'basis':identity+' 근거'}})
    schema={'type':'object','properties':{'value':{'type':'string','maxLength':120}}}
    packed,wire,aliases=prepare(prompt,schema)
    assert packed==prompt and wire==schema and aliases=={}
