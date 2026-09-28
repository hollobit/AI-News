import multiprocessing
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from llm_runtime import LLMRuntime, runtime_status


def worker(path, counters=None):
    with LLMRuntime(path,poll_seconds=.01).slot('analysis',20,10) as ticket:
        if counters is not None:
            with counters.get_lock():
                counters[0]+=1
                counters[1]=max(counters[1],counters[0])
        time.sleep(.06)
        if counters is not None:
            with counters.get_lock():counters[0]-=1
        ticket.output_chars=30


class RuntimeTests(unittest.TestCase):
    def test_interactive_question_uses_reserved_slot_while_bulk_waits(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            runtime=LLMRuntime(Path(directory)/'runtime.db',limit=2,poll_seconds=.005)
            entered=threading.Event()
            def bulk():
                with runtime.slot('analysis',1,1):entered.set()
            with runtime.slot('analysis',1,1):
                thread=threading.Thread(target=bulk);thread.start()
                time.sleep(.03)
                self.assertFalse(entered.is_set())
                with runtime.slot('graph_answer',1,1):
                    self.assertEqual(runtime_status(runtime.path)['active'],2)
                    self.assertFalse(entered.is_set())
            thread.join(2);self.assertFalse(thread.is_alive())
            self.assertTrue(entered.is_set())

    def test_cross_process_budget_and_private_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'runtime.db')
            LLMRuntime(path,limit=2)
            context=multiprocessing.get_context('spawn')
            counters=context.Array('i',[0,0])
            processes=[context.Process(target=worker,args=(path,counters)) for _ in range(5)]
            for process in processes:process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode,0)
            report=runtime_status(path)
            self.assertEqual(report['counts']['complete'],5)
            self.assertLessEqual(counters[1],2)
            self.assertEqual(counters[0],0)
            events=[]
            for row in report['recent']:
                events.extend([(row['started_at'],1),(row['finished_at'],-1)])
                self.assertNotIn('prompt',row)
            active=0
            for _,change in sorted(events):
                active+=change
                self.assertLessEqual(active,2)
            self.assertEqual(report['active'],0)

    def test_admission_timestamp_follows_sqlite_lock_wait(self):
        import threading
        waiting=threading.Event()
        class DelayedConnection:
            def __init__(self,db):self.db=db
            def __enter__(self):self.db.__enter__();return self
            def __exit__(self,*args):return self.db.__exit__(*args)
            def execute(self,sql,*args):
                if sql=='BEGIN IMMEDIATE':
                    waiting.set();time.sleep(.08)
                return self.db.execute(sql,*args)
        class DelayedRuntime(LLMRuntime):
            def db(self):return DelayedConnection(super().db())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'runtime.db'
            first=LLMRuntime(path,limit=1)
            delayed=DelayedRuntime(path)
            def next_call():
                with delayed.slot('verification',1,1):pass
            with first.slot('analysis',1,1):
                thread=threading.Thread(target=next_call)
                thread.start();self.assertTrue(waiting.wait(2))
            thread.join(3);self.assertFalse(thread.is_alive())
            records={row['role']:row for row in runtime_status(path)['recent']}
            self.assertGreaterEqual(records['verification']['started_at'],records['analysis']['finished_at'])

    def test_failure_and_queue_timeout_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'runtime.db'
            runtime=LLMRuntime(path,limit=1,poll_seconds=.005,queue_timeout=.02)
            with runtime.slot('analysis',1,1):
                with self.assertRaises(RuntimeError):
                    with runtime.slot('verification',1,1):pass
            with self.assertRaises(ValueError):
                with runtime.slot('analysis',1,1) as ticket:
                    ticket.error_code='invalid_output'
                    raise ValueError('secret')
            report=runtime_status(path)
            self.assertEqual(report['active'],0)
            self.assertEqual(report['counts']['failed'],2)
            self.assertNotIn('secret',str(report))

    def test_dead_owner_recovery(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            runtime=LLMRuntime(Path(directory)/'runtime.db',limit=1)
            with runtime.db() as db:
                db.execute("INSERT INTO llm_calls(role,status,owner_pid,queued_at,input_chars,schema_chars) VALUES ('analysis','running',999999999,?,1,1)",(time.time(),))
            with patch('llm_runtime._alive',return_value=False):
                report=runtime_status(runtime.path)
            self.assertEqual(report['active'],0)
            self.assertEqual(report['recent'][0]['error_code'],'owner_exited')

    def test_adaptive_boundaries(self):
        import json
        from bulk_baseline import choose_batch_size
        for length,expected in [(100,16),(1000,12),(2000,8)]:
            rows=[{'snapshot_json':json.dumps({'evidence':[{'text':'x'*length}]})}]
            self.assertEqual(choose_batch_size(rows,{'adaptive_batches':True}),expected)
            self.assertEqual(choose_batch_size(rows,{'batch_size':12}),12)
