import sqlite3
import unittest
import tempfile
from pathlib import Path
from dynamic_registry import list_registry,save_manual,set_excluded,sync_automatic


class RegistryTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:')
    def tearDown(self):self.db.close()
    def candidate(self,**changes):
        return dict({'id':'dynamic:abc','kind':'topic','label':'소버린 AI','terms':['소버린 AI'],
                     'metadata':{'verification':'rule_checked','evidence':[]}},**changes)

    def test_sync_stable_version_tombstone_and_restore(self):
        first=sync_automatic(self.db,[self.candidate()])[0]
        sync_automatic(self.db,[self.candidate()])
        self.assertEqual(list_registry(self.db)['version'],first['version'])
        set_excluded(self.db,first['id'],True)
        sync_automatic(self.db,[])
        restored_candidate=sync_automatic(self.db,[self.candidate()])[0]
        self.assertTrue(restored_candidate['excluded'])
        self.assertEqual(list_registry(self.db,False)['items'],[])
        restored=set_excluded(self.db,first['id'],False)
        self.assertFalse(restored['excluded'])
        self.assertEqual(restored['created_at'],first['created_at'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_topic_registry').fetchone()[0],1)

    def test_unchanged_sync_does_not_take_writer_lock(self):
        sync_automatic(self.db,[self.candidate()])
        statements=[]
        self.db.set_trace_callback(statements.append)
        sync_automatic(self.db,[self.candidate()])
        self.assertFalse(any(s.startswith('BEGIN IMMEDIATE') for s in statements))
        sync_automatic(self.db,[self.candidate(label='Changed')])
        self.assertTrue(any(s.startswith('BEGIN IMMEDIATE') for s in statements))

    def test_unchanged_sync_while_another_connection_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'registry.db'
            with sqlite3.connect(path) as writer, sqlite3.connect(path,timeout=.01) as reader:
                writer.execute('PRAGMA journal_mode=WAL')
                sync_automatic(writer,[self.candidate()])
                writer.execute('BEGIN IMMEDIATE')
                self.assertEqual(sync_automatic(reader,[self.candidate()])[0]['id'],'dynamic:abc')
                writer.rollback()

    def test_manual_override_keeps_stable_id_and_meaning(self):
        first=sync_automatic(self.db,[self.candidate()])[0]
        manual=save_manual(self.db,dict(first,label='국가 AI 자립',terms=['국가 AI 자립']))
        current=sync_automatic(self.db,[self.candidate()])[0]
        self.assertEqual(current['label'],'국가 AI 자립')
        self.assertEqual(current['origin'],'manual')
        self.assertEqual(current['id'],first['id'])
        self.assertEqual(current['version'],manual['version'])
        sync_automatic(self.db,[])
        self.assertEqual(list_registry(self.db)['items'][0]['status'],'active')

    def test_builtin_exclusion_and_separate_sync(self):
        sync_automatic(self.db,[self.candidate()])
        sync_automatic(self.db,[{'id':'kill_switch','origin':'builtin','kind':'signal','label':'Kill switch','terms':['shutdown']}],namespace='builtin')
        set_excluded(self.db,'kill_switch',True)
        values={i['id']:i for i in sync_automatic(self.db,[self.candidate()])}
        self.assertEqual(values['kill_switch']['status'],'active')
        self.assertTrue(values['kill_switch']['excluded'])
        self.assertEqual(values['dynamic:abc']['status'],'active')

    def test_validation_and_sql_literal(self):
        item=save_manual(self.db,{'label':"AI'; DROP TABLE news;--",'terms':['의료 의사결정 보조']})
        self.assertTrue(item['id'].startswith('manual:'))
        self.assertEqual(len(list_registry(self.db)['items']),1)
        for payload in [{'label':''},{'label':'x','terms':[]},{'label':'x','kind':'bad'},
                        {'label':'x','metadata':{'x':float('nan')}},{'label':'x','excluded':'false'},
                        {'label':'x','terms':['x'*101]}]:
            with self.assertRaises(ValueError):save_manual(self.db,payload)
        self.assertIsNone(set_excluded(self.db,'missing',True))

    def test_invalid_batch_is_atomic_and_history_tracks_changes(self):
        sync_automatic(self.db,[self.candidate()])
        before=list_registry(self.db)
        with self.assertRaises(ValueError):sync_automatic(self.db,[self.candidate(label='changed'),self.candidate(id='invalid')])
        self.assertEqual(list_registry(self.db),before)
        updated=sync_automatic(self.db,[self.candidate(metadata={'verification':'rule_checked','count':3})])[0]
        history=self.db.execute('SELECT payload_json FROM dynamic_topic_registry_history WHERE version=?',(updated['version'],)).fetchone()[0]
        self.assertIn('rule_checked',history)
