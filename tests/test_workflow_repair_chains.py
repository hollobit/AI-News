"""Scheduling uses real workflows and validators; model responses are deterministic."""
import threading
from collections import Counter
from unittest.mock import patch

import pytest

from strategic_workflow import WorkflowService
from workflow_synthesis import VERSION
from test_workflow_synthesis import ReviewedModel, Sources, ITEM, REQUEST
from test_workflow_efficiency import wait
from test_strategic_workflow import Model
from engine_errors import EngineError


class Rejected(ReviewedModel):
    def __init__(self, rejected=(), always=False, before=None, after=None):
        super().__init__()
        self.rejected, self.always = rejected, always
        self.before, self.after = before, after

    def __call__(self, prompt, schema):
        role = prompt.splitlines()[0][6:]
        if self.before:
            self.before(role, self)
        result = super().__call__(prompt, schema)
        if role in self.rejected and (self.always or self.calls.count(role) == 1):
            result.update(accepted=False, issues=['근거 해석 보완 필요'])
        if self.after:
            self.after(role, self)
        return result


def service(tmp_path, model, sources=None):
    instance = WorkflowService(tmp_path/'news.db', sources=sources or Sources(),
                               analyzer=model, extractor=lambda _: [], enabled=True)
    instance._retrieve_graph = lambda *_: {'status': 'complete', 'result': {}}
    return instance


def test_risk_repair_finishes_while_national_analysis_is_running(tmp_path):
    repaired = threading.Event()
    def before(role, model):
        if role == 'national':
            assert repaired.wait(2)
    def after(role, model):
        if role == 'risk_verification' and model.calls.count(role) == 2:
            assert 'national' not in model.finished
            repaired.set()
    model = Rejected(('risk_verification',), before=before, after=after)
    workflow = service(tmp_path, model)
    try:
        result = wait(workflow, workflow.create_run([ITEM], REQUEST)['id'])
        assert result['status'] == 'complete', result['error']
        assert model.calls.count('risk_revision') == 1
        assert model.calls.count('risk_verification') == 2
        assert model.calls.count('verification') == 1
        assert result['results']['verification']['reused']
        assert result['results']['orchestration']['repair_schedule'] == 'independent-fixed-evidence-v1'
    finally:
        repaired.set()
        workflow.close()


def test_strategy_repair_finishes_while_first_risk_review_is_running(tmp_path):
    repaired = threading.Event()
    def before(role, model):
        if role == 'risk_verification':
            assert repaired.wait(2)
    def after(role, model):
        if role == 'verification' and model.calls.count(role) == 2:
            assert 'risk_verification' not in model.finished
            repaired.set()
    model = Rejected(('verification',), before=before, after=after)
    workflow = service(tmp_path, model)
    try:
        result = wait(workflow, workflow.create_run([ITEM], REQUEST)['id'])
        assert result['status'] == 'complete', result['error']
        assert model.calls.count('revision') == 1
        assert model.calls.count('verification') == 2
        assert model.calls.count('risk_verification') == 1
        assert result['results']['risk_verification']['reused']
    finally:
        repaired.set()
        workflow.close()


@pytest.mark.parametrize('rejected', [('verification',), ('risk_verification',), ('verification', 'risk_verification')])
def test_rejected_recheck_cannot_gain_another_repair_cycle(tmp_path, rejected):
    model = Rejected(rejected, always=True)
    workflow = service(tmp_path, model)
    try:
        result = wait(workflow, workflow.create_run([ITEM], REQUEST)['id'])
        assert result['status'] == 'needs_review', result['error']
        assert not result['results']['verified']
        for review, revision in [('verification', 'revision'), ('risk_verification', 'risk_revision')]:
            assert model.calls.count(review) == (2 if review in rejected else 1)
            assert model.calls.count(revision) == int(review in rejected)
        assert not result['artifacts'][rejected[0]]['accepted']
    finally:
        workflow.close()


def test_failed_source_is_repaired_before_either_revision(tmp_path):
    class ChangedSources:
        fetched = 0
        def fetch(self, url, **kwargs):
            self.fetched += 1
            if self.fetched == 1:
                return dict(status='failed', url=url, text='')
            return Sources().fetch(url)
    sources = ChangedSources()
    def before(role, model):
        if role in ('revision', 'risk_revision'):
            assert 'verification' in model.finished
            assert 'risk_verification' in model.finished
            assert sources.fetched == 2
    class Legacy(Rejected):
        def __call__(self, prompt, schema):
            if prompt.startswith('ROLE: synthesis'):
                result = Model.__call__(self, prompt, schema)
                result['event_observations'] = []
                self.finished.add('synthesis')
                return result
            return super().__call__(prompt, schema)
    model = Legacy(('risk_verification',), before=before)
    workflow = service(tmp_path, model, sources)
    try:
        # A saved role plan follows the established post-review source-repair
        # route. It must not start an early revision over the old partial input.
        with patch.object(workflow, '_start'):
            run = workflow.create_run([ITEM], REQUEST)
        workflow._save(run['id'], 'execution_plan', dict(path='multi-role', reason='source_unavailable'))
        workflow.active = run['id']
        workflow._work(run['id'])
        result = workflow.get_run(run['id'])
        assert result['status'] == 'complete', result['error']
        assert len(result['results']['evidence']) == 2
        assert 'repair_schedule' not in result['results']['orchestration']
        assert model.calls.count('revision') == model.calls.count('risk_revision') == 1
        assert model.calls.count('verification') == model.calls.count('risk_verification') == 2
        assert result['results']['verification']['evidence_hash'] != result['artifacts']['verification']['evidence_hash']
    finally:
        workflow.close()


def test_capacity_failure_resumes_only_unfinished_early_risk_recheck(tmp_path):
    model = Rejected(('risk_verification',))
    calls = Counter()
    failed = False
    def analyze(prompt, schema):
        nonlocal failed
        role = prompt.splitlines()[0][6:]
        calls[role] += 1
        if role == 'risk_verification' and calls[role] == 2:
            failed = True
            raise EngineError('capacity')
        return model(prompt, schema)
    workflow = service(tmp_path, analyze)
    try:
        first = wait(workflow, workflow.create_run([ITEM], REQUEST)['id'])
        assert first['status'] == 'failed' and failed
        assert 'risk_revision' in first['artifacts'] and 'verification' in first['artifacts']
        before = calls.copy()
        second = wait(workflow, workflow.resume(first['id'])['id'])
        assert second['status'] == 'complete', second['error']
        assert calls - before == Counter(risk_verification=1)
        assert calls['risk_revision'] == 1
        assert second['artifacts']['risk_verification']['accepted'] is False
        assert second['results']['risk_verification']['accepted'] is True
    finally:
        workflow.close()


def test_role_projection_keeps_early_risk_repair_input_identical():
    from workflow_prompts import render
    evidence = [dict(id='one', text='현재 원자료')]
    core = dict(coverage={'failed_urls': []}, request={'completion': True},
                risk_report={'summary': '기존 위험'}, critique={'issues': ['보완']})
    full = dict(core, keywords=[{'evidence_id': 'one', 'keywords': []}], graph_context={'status': 'complete'},
                deliberation={'questions': []}, risk_review_status='pending')
    assert render('risk_revision', evidence, core) == render('risk_revision', evidence, full)
