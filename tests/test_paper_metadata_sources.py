import json,sqlite3
import pytest
from paper_metadata_sources import init,save,snapshot_record,openalex_record,parse_oai
from arxiv_papers import init_papers,paper
from paper_analysis import PaperAnalysisService,analysis_input_hash,digest
from paper_graph import validated_paper_analysis

ID='1804.07389'
def snapshot():return snapshot_record({'id':ID,'title':'Example research','abstract':'The authors report an experiment.','authors_parsed':[['Smith','Alice','']],'categories':'cs.AI cs.LG','versions':[{'version':'v1','created':'Mon, 9 Apr 2018 18:00:00 GMT'}]})
def indexed():return openalex_record({'id':'https://openalex.org/W1','doi':'https://doi.org/10.48550/arxiv.'+ID,'title':'Example research','abstract_inverted_index':{'The':[0],'authors':[1],'report':[2],'results.':[3]},'authorships':[],'publication_date':'2018-04-09'},ID)

def test_identity_and_complete_abstract_required():
    row={'id':'https://openalex.org/W1','title':'Wrong','doi':'https://doi.org/10.48550/arxiv.9999.12345','abstract_inverted_index':{'x':[0]}}
    with pytest.raises(ValueError,match='식별자'):openalex_record(row,ID)
    row['doi']='https://doi.org/10.48550/arxiv.'+ID;row['abstract_inverted_index']={'x':[1]}
    with pytest.raises(ValueError,match='누락'):openalex_record(row,ID)

def test_secondary_does_not_overwrite_official_and_old_version_does_not_regress():
    db=sqlite3.connect(':memory:');init_papers(db);init(db)
    db.execute('INSERT INTO arxiv_papers(paper_id) VALUES(?)',(ID,))
    assert save(db,indexed())
    assert save(db,snapshot())
    assert not save(db,indexed())
    newer=dict(snapshot(),metadata_version=2);assert save(db,newer)
    assert not save(db,snapshot())
    assert paper(db,ID)['metadata_source']=='arxiv_kaggle'
    assert db.execute('SELECT count(*) FROM paper_metadata_versions').fetchone()[0]==3
    db.close()

def test_oai_record_and_cursor_provenance():
    xml=b'''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><responseDate>2026-09-17T00:00:00Z</responseDate><ListRecords><record><header><identifier>oai:arXiv.org:1804.07389</identifier></header><metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>1804.07389</id><title>Research</title><abstract>Real abstract</abstract><authors>A Smith</authors><categories>cs.AI</categories><version version="v1"><date>Mon, 9 Apr 2018 18:00:00 GMT</date></version></arXivRaw></metadata></record><resumptionToken>next-page</resumptionToken></ListRecords></OAI-PMH>'''
    records,deleted,cursor,day=parse_oai(xml)
    assert records[0]['metadata_version']==1 and records[0]['published'].startswith('2018-04-09')
    assert records[0]['metadata_source']=='arxiv_oai' and cursor=='next-page' and day=='2026-09-17'
    with pytest.raises(ValueError):parse_oai(b'<!DOCTYPE x><x/>')

def test_secondary_analysis_evidence_cannot_masquerade_as_arxiv(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        init_papers(db);init(db);db.execute('INSERT INTO arxiv_papers(paper_id) VALUES(?)',(ID,));save(db,indexed());item=paper(db,ID)
    service=PaperAnalysisService(path,enabled=False,fetcher=lambda url:pytest.fail('Secondary abstract analysis must not fetch arXiv HTML'))
    evidence=service._evidence(item)['evidence'];assert evidence[0]['origin']=='scholarly_index_abstract'
    report={'summary':'저자 보고','claims':[{'category':'reported_result','title':'연구 결과','detail':'저자가 결과를 보고함','uncertainty':'색인 초록 범위','evidence_ids':[evidence[0]['id']]}],'limitations':['외부 색인 초록']}
    result={'paper_id':ID,'version':None,'input_hash':analysis_input_hash(item),'verified':True,'report':report,'evidence':evidence,'keywords':[],
            'verification':{'accepted':True,'issues':[],'report_hash':digest(report),'evidence_hash':digest(evidence),'checked_evidence_ids':[evidence[0]['id']]}}
    row={'status':'complete','error':'','input_hash':analysis_input_hash(item),'result_json':json.dumps(result)}
    assert validated_paper_analysis(row,item)
    evidence[0]['origin']='arxiv_abstract';result['verification']['evidence_hash']=digest(evidence);row['result_json']=json.dumps(result)
    assert not validated_paper_analysis(row,item)
    service.close()
