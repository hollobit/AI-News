import sqlite3
from projection_cache import read_projections
from projection_store import document_rows


def test_changed_and_deleted_documents_only_and_restart_reuse(tmp_path):
    path = tmp_path / 'news.db'
    built = []
    def build(keys):
        built.append(list(keys))
        return {k: {'topics': ['medical'], 'revision': len(built)} for k in keys}
    with sqlite3.connect(path) as db:
        a = document_rows(db, 'membership', {'a': 'first', 'b': 'first'}, build)
    # A fresh connection simulates a process restart; unchanged results survive.
    with sqlite3.connect(path) as db:
        b = document_rows(db, 'membership', {'a': 'second', 'b': 'first', 'c': 'new'}, build)
        assert b['b'] == a['b']
        document_rows(db, 'membership', {'a': 'second', 'c': 'new'}, build)
    assert built == [['a', 'b'], ['a', 'c']]
    with sqlite3.connect(str(path) + '.features.sqlite3') as store:
        assert store.execute("SELECT kind FROM document_projection_changes WHERE document_id='b' ORDER BY seq DESC LIMIT 1").fetchone()[0] == 'delete'
        assert store.execute('SELECT COUNT(*) FROM document_projections').fetchone()[0] == 2


def test_http_missing_projection_never_writes_and_rules_change_invalidates(tmp_path):
    path = tmp_path / 'news.db'
    with sqlite3.connect(path) as db:
        build = lambda keys: {k: {'value': k} for k in keys}
        with read_projections():
            assert document_rows(db, 'document', {'a': ['rules-v1','text']}, build)
        assert not (tmp_path / 'news.db.features.sqlite3').exists()
        document_rows(db, 'document', {'a': ['rules-v1','text']}, build)
        called = []
        def changed(keys):
            called.extend(keys)
            return {k: {'value': 'new rule'} for k in keys}
        with read_projections():
            result = document_rows(db, 'document', {'a': ['rules-v2','text']}, changed)
        assert called == ['a'] and result['a']['value'] == 'new rule'
        with sqlite3.connect(str(path) + '.features.sqlite3') as store:
            assert store.execute('SELECT COUNT(*) FROM document_projection_changes').fetchone()[0] == 1


def test_strategy_reuses_unchanged_documents_and_changed_source_recomputes(tmp_path):
    from unittest.mock import patch
    from keyword_index import keyword_record_id
    from strategy import focused_items
    from strategy_projection import prepare_documents
    items = [dict(chat_id=1,message_id=i,item_index=0,day='2026-09-29',title='의료 AI 연구',
        text='AI 의료 진단 연구',source_url=f'https://example.org/{i}',source_context={}) for i in (1,2)]
    mappings = {keyword_record_id(i): [] for i in items}
    with sqlite3.connect(tmp_path/'news.db') as db, patch('strategy.focused_items', wraps=focused_items) as focus:
        first,morph = prepare_documents(db,items,mappings)
        second,again = prepare_documents(db,items,mappings)
        assert second==first and again==morph and focus.call_count==1
        items[0]['source_context']={'status':'fetched','title':'변경된 연구','text':'공공 AX 행정 AI'}
        changed,_ = prepare_documents(db,items,mappings)
        assert focus.call_count==2 and len(focus.call_args.args[0])==1
        assert changed[1]==first[1] and changed[0]!=first[0]
