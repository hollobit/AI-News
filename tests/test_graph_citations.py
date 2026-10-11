from graph_citations import pack,restore


def test_wire_references_restore_without_rewriting_original_source_or_colliding():
    identity='evidence:external:'+'a'*64
    source={'id':identity,'title':'CITE1 실제 명칭','text':'원문에 등장한 '+identity,'source_url':'https://example.org/?id='+identity}
    data={'question':'CITE 실험은?', 'evidence':[source], 'claims':[{'text':'실제 설명','evidence_ids':[identity]}]}
    schema={'properties':{'evidence_ids':{'items':{'type':'string','enum':[identity]}}}}
    wire,contract,aliases=pack(data,schema)
    short=wire['evidence'][0]['id']
    assert len(short)<len(identity) and short!='CITE1'
    assert wire['evidence'][0]==dict(source,id=short)
    assert wire['claims'][0]['evidence_ids']==[short]
    assert contract['properties']['evidence_ids']['items']['enum']==[short]
    restored=restore({'text':'근거 ['+short+'] 확인','evidence_ids':[short],'unchanged':'X_'+short+'_X'},aliases)
    assert restored=={'text':'근거 ['+identity+'] 확인','evidence_ids':[identity],'unchanged':'X_'+short+'_X'}
    assert data['evidence'][0]==source and schema['properties']['evidence_ids']['items']['enum']==[identity]


def test_unknown_alias_is_not_restored_into_a_valid_citation():
    _,_,aliases=pack({'evidence':[{'id':'original'}]}, {})
    assert restore({'evidence_ids':['CITE999']},aliases)['evidence_ids']==['CITE999']
