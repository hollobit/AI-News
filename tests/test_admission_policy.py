"""Writer edits retain admitted originals, never silently retain an old answer policy."""
from pathlib import Path
from unittest.mock import Mock
import json
import sqlite3
import time

import pytest
from code_policy import digest, question_only, QUESTION_ONLY
from graph_questions import GraphQuestions,answer_key,answer_day
from graph_snapshot import revision
from graph_evidence_selection import VERSION
from projection_cache import cached_read


def tree(root):
    (root/'source_projection.py').write_text('from graph_rag import _analysis_sources\n')
    (root/'graph_rag.py').write_text('def answer_question():\n from graph_answer_quality import PROFILE\n')
    for module in QUESTION_ONLY:(root/(module+'.py')).write_text('VALUE = 1\n')
    return root


def policies(root):
    return {scope:digest(scope,root) for scope in ('source','admission','validation','publication')}


@pytest.mark.parametrize('module',sorted(QUESTION_ONLY))
def test_only_question_policy_changes_for_question_only_edits(tmp_path,module):
    root=tree(tmp_path);before=policies(root)
    (root/(module+'.py')).write_text('VALUE = 2\n')
    after=policies(root)
    assert before['source']==after['source'] and before['admission']==after['admission']
    assert before['validation']!=after['validation'] and before['publication']!=after['publication']


@pytest.mark.parametrize('module',['baseline_graph','paper_graph','workflow_results','unknown_new_gate','graph_rag','graph_plan_router','model_policy'])
def test_admission_gates_and_unknown_modules_still_invalidate(tmp_path,module):
    root=tree(tmp_path);before=policies(root)
    with (root/(module+'.py')).open('a') as out:out.write('# admission edit\n')
    assert before['admission']!=digest('admission',root)


@pytest.mark.parametrize('consumer',[
    'from graph_answer_quality import sufficient\n',
    'def admit():\n import graph_answer_format\n',
    'class Gate:\n def admit(self):\n  from graph_review_format import restore\n'])
def test_new_admission_dependency_fails_back_to_full_fingerprinting(tmp_path,consumer):
    root=tree(tmp_path);(root/'source_projection.py').write_text(consumer)
    assert not question_only(list(root.glob('*.py')))
    before=policies(root)
    (root/'graph_answer_quality.py').write_text('VALUE = 2\n')
    assert before['admission']!=digest('admission',root)
    if 'graph_answer_quality' in consumer:assert before['source']!=digest('source',root)


def test_mixed_graph_module_cannot_move_a_question_dependency_into_admission(tmp_path):
    root=tree(tmp_path)
    (root/'graph_rag.py').write_text('def _analysis_sources(db):\n from graph_answer_quality import sufficient\n')
    assert not question_only(list(root.glob('*.py')))


def test_production_question_boundaries_match_audited_allowlist():
    assert question_only(list(Path(__file__).resolve().parents[1].glob('*.py')))==QUESTION_ONLY


def test_source_index_and_validation_cache_survive_writer_edits_but_answer_does_not(tmp_path,monkeypatch):
    root=tmp_path/'code';root.mkdir();tree(root)
    monkeypatch.setattr('verified_cache.policy',lambda scope='validation':digest(scope,root))
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE articles(id INTEGER PRIMARY KEY,text TEXT)')
        db.execute('INSERT INTO articles VALUES(1,\'original\')')
    params={'workspace':['1']};request=dict(question='현재 자료의 비교 조건은?',params=params,node_ids=[])
    service=GraphQuestions(path,enabled=False)
    before=revision(path,params);old_policy=digest('validation',root);key=answer_key(before,request)
    builder=Mock(return_value={'accepted':True})
    with sqlite3.connect(path) as db:assert cached_read(db,'paper_verified_content','same-input',builder)=={'accepted':True}
    current=Mock(return_value=True);monkeypatch.setattr('context_current.current',current)
    saved=dict(answer='이전 설명',answer_as_of=answer_day(),workspace_dependencies={'source':'hash'},
        workspace_policy=old_policy,workspace_as_of=time.time(),retrieval={'policy':VERSION},
        verification={'method':'independent_evidence_review','quality':{'policy':'question-explanation-v1','status':'passed'}})
    try:
        with service.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                ('old',key,before,json.dumps(request,ensure_ascii=False),'complete',json.dumps(saved),'',time.time(),0))
        assert service.get('old')['status']=='complete'
        (root/'graph_answer_format.py').write_text('VALUE = 2\n')
        assert revision(path,params)==before
        assert answer_key(before,request)!=key
        assert service.get('old')['status']=='stale'
        with sqlite3.connect(path) as db:
            assert cached_read(db,'paper_verified_content','same-input',builder)=={'accepted':True}
            assert builder.call_count==1
            cached_read(db,'paper_verified_content','changed-input',builder)
            assert builder.call_count==2
        # Actual originals and admission code changes still invalidate the index.
        with sqlite3.connect(path) as db:db.execute("UPDATE articles SET text='changed original'")
        assert revision(path,params)!=before
        current_revision=revision(path,params)
        (root/'paper_graph.py').write_text('# changed paper admission\n')
        assert revision(path,params)!=current_revision
        with sqlite3.connect(path) as db:cached_read(db,'paper_verified_content','same-input',builder)
        assert builder.call_count==3
    finally:service.close()
