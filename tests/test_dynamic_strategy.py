from unittest.mock import patch
import sqlite3
from dynamic_strategy import dynamic_projection, discovery_followups
from dynamic_registry import sync_automatic,set_excluded


def test_explicit_none_revision_bypasses_cache_without_reading_new_source():
    with sqlite3.connect(':memory:') as db:
        with patch('dynamic_strategy.revision_token',side_effect=AssertionError('snapshot must stay None')), patch('dynamic_topics.build_dynamic_topics',return_value={'topics':[],'signals':[]}), patch('dynamic_cooccurrence.build_cooccurrence_candidates',return_value=[]):
            assert dynamic_projection(db,[],{},None)['topics']==[]


def test_followup_queue_uses_only_active_observations_and_honors_exclusions():
    with sqlite3.connect(':memory:') as db:
        candidate={'id':'dynamic:sample','label':'AI 규제','terms':['AI 규제'],
                   'metadata':{'current':3,'evidence':[{'quote':'AI 규제'}]}}
        sync_automatic(db,[candidate])
        tasks=discovery_followups(db)
        assert len(tasks)==1 and tasks[0]['search_terms']==['AI 규제']
        assert tasks[0]['epistemic_status']=='rule_checked_observation'
        set_excluded(db,'dynamic:sample',True)
        assert discovery_followups(db)==[]


def test_exclusion_and_manual_changes_survive_new_connection(tmp_path):
    from dynamic_registry import list_registry,save_manual
    path=tmp_path/'registry.sqlite3'
    with sqlite3.connect(path) as db:
        sync_automatic(db,[{'id':'dynamic:restart','label':'원문 관측','terms':['원문 관측']}])
        set_excluded(db,'dynamic:restart',True)
        manual=save_manual(db,{'label':'직접 관측','terms':['직접 표현']})
    with sqlite3.connect(path) as db:
        sync_automatic(db,[{'id':'dynamic:restart','label':'원문 관측','terms':['원문 관측']}])
        entries={entry['id']:entry for entry in list_registry(db)['items']}
        assert entries['dynamic:restart']['excluded'] is True
        assert entries[manual['id']]['terms']==['직접 표현']


def test_automatic_metrics_do_not_invalidate_discovery_but_rule_changes_do(tmp_path):
    import sqlite3
    from unittest.mock import patch
    registry={'version':1,'items':[{'id':'dynamic:a','origin':'auto','excluded':False,'version':1}]}
    with sqlite3.connect(tmp_path/'cache.db') as db, \
         patch('dynamic_registry.list_registry',side_effect=lambda db:registry), \
         patch('dynamic_topics.build_dynamic_topics',return_value={'topics':[],'signals':[]}) as build, \
         patch('dynamic_cooccurrence.build_cooccurrence_candidates',return_value=[]):
        dynamic_projection(db,[],{},('source',1))
        registry['version']=2;registry['items'][0]['version']=2
        dynamic_projection(db,[],{},('source',1))
        assert build.call_count==1
        registry['items'][0]['excluded']=True
        dynamic_projection(db,[],{},('source',1))
        assert build.call_count==2
