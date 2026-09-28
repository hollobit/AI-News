import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

from engine_errors import EngineError, classify_failure, infrastructure_error
from llm_runtime import LLMRuntime, runtime_status
from corpus_completion import CompletionRunner, connect, init, recover_engine_failures


class EngineRecoveryTests(unittest.TestCase):
    def test_requested_continuation_recovers_exact_database_lock_only(self):
        import engine_errors as module
        paused = {'status': 'paused', 'owner_pid': None}
        self.assertTrue(module.deep_engine_pause(dict(paused, error='database is locked')))
        self.assertTrue(module.deep_engine_pause(dict(paused, error=str(EngineError('database_locked')))))
        self.assertFalse(module.deep_engine_pause(dict(paused, error='기사에 database is locked 문구가 없습니다')))
        self.assertFalse(module.deep_engine_pause(dict(paused, error=str(EngineError('rate_limit')))))
        self.assertFalse(module.deep_engine_pause(dict(paused, pause_requested=1, error='database is locked')))
        self.assertFalse(module.deep_engine_pause({'status': 'running', 'owner_pid': None,
                                                   'error': 'database is locked'}))

    def test_database_contention_is_not_a_content_review_failure(self):
        for message in ('database is locked', 'database table is locked'):
            self.assertEqual(infrastructure_error(message), 'database_locked')
        self.assertIsNone(infrastructure_error('기사에 database is locked 문구가 없습니다'))
        self.assertEqual(infrastructure_error(str(EngineError('database_locked'))), 'database_locked')

    def test_diagnostics_do_not_expose_provider_text(self):
        for text, code in [('HTTP 401 token_invalidated secret', 'authentication'),
                           ('ERROR: Selected model is at capacity. Please try a different model.', 'capacity'),
                           ('You have hit your usage limit sk-secret', 'rate_limit'),
                           ('failed to initialize: Operation not permitted', 'permission'),
                           ('stream disconnected connection reset', 'network'),
                           ('unknown feature x', 'configuration')]:
            self.assertEqual(classify_failure(text),code)
            self.assertNotIn('secret',str(EngineError(code)))
            self.assertEqual(infrastructure_error(str(EngineError(code))),code)
        self.assertIsNone(infrastructure_error('인용 근거 누락'))
        self.assertEqual(classify_failure('analysis mentions authentication and usage limits\nERROR: stream disconnected'), 'network')

    def test_cli_echoed_evidence_does_not_contaminate_error_classification(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from semantic import _run_structured_cli
        prompt='Article about authentication, quota, and unsupported models.'
        ticket=SimpleNamespace(error_code='',output_chars=0)
        failed=SimpleNamespace(returncode=1,stderr=prompt+'\nERROR: stream disconnected: connection reset')
        with patch('semantic.subprocess.run',return_value=failed):
            with self.assertRaisesRegex(EngineError,'engine:network'):
                _run_structured_cli(prompt,{},'codex',ticket)
        self.assertEqual(ticket.error_code,'network')

    def test_repeated_outage_blocks_bulk_and_successful_probe_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=LLMRuntime(Path(directory)/'runtime.db')
            for _ in range(3):
                with self.assertRaises(EngineError):
                    with runtime.slot('analysis',1,1) as ticket:
                        ticket.error_code='network'
                        raise EngineError('network')
            with self.assertRaisesRegex(EngineError,'circuit_open'):
                with runtime.slot('analysis',1,1):self.fail('Must not start another CLI')
            with runtime.slot('engine_probe',1,1):pass
            with runtime.slot('analysis',1,1):pass
            self.assertEqual(runtime_status(runtime.path)['active'],0)

    def test_explicit_recovery_preserves_history_and_never_resets_review_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory)/'news.db') as db:
                init(db)
                db.execute('CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT)')
                for index,error in enumerate(['분석 엔진 실행에 실패했습니다.','실제 자료에 없는 인용']):
                    identity=str(index)
                    db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?)',(identity,'failed',error))
                    db.execute('INSERT INTO corpus_completion_documents VALUES (?,?,?,?,?,?,?,?,?)',
                        ('cycle',identity,index,'{}','failed',2,identity,'{}','before'))
                self.assertEqual(recover_engine_failures(db,'cycle'),1)
                self.assertEqual(recover_engine_failures(db,'cycle'),0)
                rows=db.execute('SELECT status,attempts FROM corpus_completion_documents ORDER BY position').fetchall()
                self.assertEqual([tuple(row) for row in rows],[('pending',0),('failed',2)])
                before=json.loads(db.execute('SELECT prior_json FROM corpus_engine_recoveries').fetchone()[0])
                self.assertEqual(before['attempts'],2)
                self.assertEqual(before['status'],'failed')

    def test_infrastructure_failure_preserves_round_checkpoint_without_consuming_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'news.db'
            with connect(path) as db:
                db.execute('CREATE TABLE rsi_rounds(id TEXT,status TEXT,error TEXT)')
                db.execute("INSERT INTO rsi_rounds VALUES ('round','running','')")
            runner=CompletionRunner(path,'cycle')
            runner.record({'id':'round','number':1},{'status':'failed','error':str(EngineError('network'))})
            self.assertTrue(runner.stop.is_set())
            self.assertIn('[engine:network]',runner.engine_error)
            with connect(path) as db:
                self.assertEqual(db.execute('SELECT status FROM rsi_rounds').fetchone()[0],'planned')

    def test_orphan_engine_failure_is_requeued_but_active_checkpoint_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory)/'news.db') as db:
                init(db)
                db.execute('CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT)')
                db.execute('CREATE TABLE rsi_rounds(cycle_id TEXT,status TEXT,snapshot_json TEXT)')
                db.execute('INSERT INTO rsi_rounds VALUES (?,?,?)',
                           ('cycle','running',json.dumps({'identities':['active']})))
                db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?)',
                           ('failed-run','failed',str(EngineError('network'))))
                for i,identity in enumerate(['active','orphan']):
                    db.execute('INSERT INTO corpus_completion_documents VALUES (?,?,?,?,?,?,?,?,?)',
                        ('cycle',identity,i,'{}','running',2,'failed-run','{}','before'))
                self.assertEqual(recover_engine_failures(db,'cycle'),1)
                self.assertEqual(recover_engine_failures(db,'cycle'),0)
                states={r['document_id']:(r['status'],r['attempts']) for r in db.execute('SELECT * FROM corpus_completion_documents')}
                self.assertEqual(states,{'active':('running',2),'orphan':('pending',0)})
                saved=json.loads(db.execute('SELECT prior_json FROM corpus_engine_recoveries').fetchone()[0])
                self.assertEqual(saved['status'],'running')
