"""One correction must reach live lists, maps, prepared search and answer dependencies."""
import copy,json,sqlite3
from unittest.mock import patch

import pytest

from evidence_corrections import correction_token,risk_projection_token,risk_corrections
from graph_rag import _workflow_graph
from integrated_context import enrich_sources
from risk_analysis import _digest,read_risks
from risk_graph import load_risk_graph
from risk_views import read_risk_page,read_risk_detail
from tests.test_risk_analysis import fixture


@pytest.fixture
def archive(tmp_path,monkeypatch):
    db=sqlite3.connect(tmp_path/'archive.db');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT,updated_at TEXT);
      CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT);
      CREATE TABLE risk_review_resolutions(risk_id TEXT,status TEXT,reason TEXT,corrected_source_url TEXT,replacement_workflow_run_id TEXT,created_at TEXT);''')
    payload=fixture()
    for run in ('old','new'):
        db.execute('INSERT INTO strategic_workflow_runs VALUES (?,\'complete\',\'\',\'2026-10-19\')',(run,))
        db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,\'final\',?)',(run,json.dumps(payload)))
    db.commit()
    monkeypatch.setattr('knowledge_wiki.read_wiki',lambda *a,**k:{'pages':[]})
    yield db,payload
    db.close()


def resolve(db,payload,status='withdrawn_analysis_error',reason='독립 재검토에서 해석 오류 확인'):
    risk_id='risk:'+_digest(['old',payload['risk_report']['risks'][0]])[:24]
    db.execute('INSERT INTO risk_review_resolutions VALUES (?,?,?,?,?,?)',(risk_id,status,reason,'https://example.com/a','new','2026-10-19T00:00:00Z'))
    db.commit();return risk_id


def source(payload,run):
    return dict(kind='workflow',id=run,row={},result=_workflow_graph(payload,run))


def risk_nodes(sources):
    return [n for s in sources for n in s['result']['nodes'] if n['type'] in ('RiskAssessment','ConditionalScenario')]


def test_content_withdrawal_keeps_originals_and_valid_strategy_across_live_views(archive):
    db,payload=archive;original=json.dumps(payload,sort_keys=True)
    identity=resolve(db,payload)
    raw=read_risks(db,include_history=True)['risks']
    assert len(raw)==2 and len(read_risks(db)['risks'])==1
    assert _workflow_graph(payload,'old',correction_token(db))==_workflow_graph(payload,'old')
    enriched=enrich_sources(db,[source(payload,'old'),source(payload,'new')])
    assert len(risk_nodes(enriched))==2
    assert not any(s['id']=='old:risks' for s in enriched)
    assert json.dumps(payload,sort_keys=True)==original
    with patch('risk_views._replacement_verified',return_value=False):
        # The two identical archived reports are deduplicated in the list, so
        # use distinct titles for separate production entries in other tests.
        old=read_risk_detail(db,identity)['risk']
        assert old['lifecycle']=='withdrawn_analysis_error'
        assert old['resolution']['verification_status']=='pending_resolution'
        assert read_risk_page(db,{'status':['withdrawn']})['total']==1
        graph=load_risk_graph(db)
        assert not any(n['id']==identity for n in graph['nodes'])
        historical=load_risk_graph(db,{'status':['withdrawn']})
        assert any(n['id']==identity for n in historical['nodes'])
    assert db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id='old'").fetchone()[0]==json.dumps(payload)


@pytest.mark.parametrize('status',['context_reassessment','source_correction'])
def test_replacement_status_needs_current_independent_verification(archive,status):
    db,payload=archive;resolve(db,payload,status=status)
    with patch('risk_views._replacement_verified',return_value=False):
        assert risk_projection_token(db)==()
        assert len(read_risks(db)['risks'])==2
        assert len(risk_nodes(enrich_sources(db,[source(payload,'old')])))==2
    with patch('risk_views._replacement_verified',return_value=True):
        # A replacement audit/source change advances the real revision key.
        db.execute("UPDATE strategic_workflow_artifacts SET payload_json=payload_json WHERE run_id='new'")
        db.commit()
        assert len(risk_projection_token(db))==1
        assert len(read_risks(db)['risks'])==1
        assert not risk_nodes(enrich_sources(db,[source(payload,'old')]))


@pytest.mark.parametrize('status,reason',[('resolved','text'),('withdrawn_analysis_error','')])
def test_unknown_or_unreasoned_record_does_not_remove_risk(archive,status,reason):
    db,payload=archive;resolve(db,payload,status,reason)
    assert risk_projection_token(db)==()
    assert len(read_risks(db)['risks'])==2


def test_selected_answer_does_not_reaudit_unrelated_replacements(archive,monkeypatch):
    from context_current import load,identity
    db,payload=archive;resolve(db,payload,status='context_reassessment')
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db,**kw:([source(payload,run) for run in kw['selected']['workflow']],{}))
    with patch('risk_views._replacement_verified',side_effect=AssertionError('Unrelated replacement rechecked')):
        current=load(db,[identity(source(payload,'new'))])
        assert len(current)==1 and current[0]['workspace_risk_corrections']==[]
    with patch('risk_views._replacement_verified',return_value=True) as check:
        current=load(db,[identity(source(payload,'old'))])
        assert len(current[0]['workspace_risk_corrections'])==1
        check.assert_called_once()


def test_correction_survives_immutable_workspace_and_invalidates_old_answer_dependencies(archive,tmp_path,monkeypatch):
    from context_index import write,read,materialize
    from context_current import load,dependencies
    db,payload=archive
    sources=lambda db,**kwargs:([source(payload,'old')],{})
    monkeypatch.setattr('graph_rag._analysis_sources',sources)
    key=json.dumps(['workflow','old',None],separators=(',',':'))
    expected=dependencies(load(db,[key]));assert expected
    before=tmp_path/'before.db';write(before,db,'before')
    assert any(n['type']=='ConditionalScenario' for n in materialize(read(before,'before'),'접근 통제').full_nodes)
    resolve(db,payload)
    current=load(db,[key]);assert dependencies(current)!=expected
    assert current[0]['workspace_risk_corrections']==risk_corrections(payload,'old',risk_projection_token(db))
    after=tmp_path/'after.db';write(after,db,'after')
    graph=materialize(read(after,'after'),'접근 통제')
    assert not any(n['type'] in ('RiskAssessment','ConditionalScenario') for n in graph.full_nodes)
    assert any(n['type']=='StrategicClaim' for n in graph.full_nodes)
    assert graph.full_evidence
    db.execute('DELETE FROM risk_review_resolutions');db.commit()
    assert dependencies(load(db,[key]))==expected


@pytest.mark.parametrize('invalid',[None,[],{'risks':None}])
def test_invalid_risk_report_does_not_crash_prepared_index(archive,tmp_path,monkeypatch,invalid):
    from context_index import write,read,materialize
    db,payload=archive
    payload['risk_report']=invalid
    db.execute('UPDATE strategic_workflow_artifacts SET payload_json=?',(json.dumps(payload),));db.commit()
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db,**kw:([source(payload,'old')],{}))
    path=tmp_path/'index.db';write(path,db,'current')
    graph=materialize(read(path,'current'),'접근 통제')
    assert not any(n['type'] in ('RiskAssessment','ConditionalScenario') for n in graph.full_nodes)
    # Preserve the existing overall audit gate when the saved risk report is
    # malformed; neither its strategy nor its risk may be newly admitted.
    assert not any(n['type']=='StrategicClaim' for n in graph.full_nodes)


@pytest.mark.parametrize('verified',[True,False])
def test_public_snapshot_follows_verified_replacement_without_rewriting_ledger(archive,monkeypatch,verified):
    from public_site import content
    from improvement_selection import content_identity
    db,payload=archive
    revised=copy.deepcopy(payload);revised['risk_report']['risks'][0]['title']='재검토한 접근 통제'
    revised['risk_verification']['report_hash']=_digest(revised['risk_report'])
    db.execute("UPDATE strategic_workflow_artifacts SET payload_json=? WHERE run_id='new'",(json.dumps(revised),))
    db.execute('CREATE TABLE corpus_completion_documents(document_id TEXT,workflow_run_id TEXT,status TEXT)')
    item=dict(title='',text=payload['evidence'][0]['text'],source_url='https://example.com/a',day='2026-09-10')
    db.execute("INSERT INTO corpus_completion_documents VALUES (?,'old','complete')",(content_identity(item),));db.commit()
    resolve(db,payload)
    monkeypatch.setattr('baseline_graph.baseline_sources',lambda *a:([],{}))
    monkeypatch.setattr('completion_quality.document_admission',lambda *a:{'verified':True,'risk_reviewed':True})
    with patch('risk_views._replacement_verified',return_value=verified):
        public=content(db,news_only=True,selected_items=[item])
    assert [r['title'] for r in public['risks']]==(['재검토한 접근 통제'] if verified else [])
    assert any(a['kind']=='심층 분석' for a in public['news'][0]['analyses'])
    assert db.execute('SELECT workflow_run_id FROM corpus_completion_documents').fetchone()[0]=='old'
