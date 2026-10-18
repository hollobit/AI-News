import sqlite3
from unittest.mock import patch

import pytest
from app import connect
from bulk_baseline import freeze_item
from context_current import corpus
from improvement_selection import all_corpus_items
from projection_cache import revision_token
from source_changes import install
from source_projection import selected_documents
from tests.test_source_projection import post


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / 'news.db'
    db = connect(path)
    install(db)
    db.execute('CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY,document_id TEXT)')
    db.commit()
    revision_token(db)
    for index in range(12):
        post(db, index, f'공공 AI 기사 {index}\nhttps://example.org/{index}?id=1&part=2')
    post(db, 20, 'URL 없는 의료 AI 관측')
    post(db, 21, '숨은 링크 기사', entities=[{'type': 'text_link', 'offset': 0, 'length': 2, 'url': 'https://example.org/hidden'}])
    items = all_corpus_items(db)
    for item in items:
        frozen = freeze_item(item)
        db.execute('INSERT INTO bulk_baseline_cache VALUES(?,?)', (frozen['input_hash'], frozen['document_id']))
    db.commit()
    yield path, db, items
    db.close()


def test_cold_admission_loads_only_selected_url_hidden_and_url_free_items(archive):
    path, _, items = archive
    chosen = [item for item in items if item['source_url'] in ('https://example.org/hidden', 'https://example.org/3?id=1&part=2')
              or (not item['source_url'] and item['message_id'] == 20)]
    assert len(chosen) == 3
    hashes = {freeze_item(item)['input_hash']: item for item in chosen}
    with sqlite3.connect(path) as reader, patch('bulk_baseline.freeze_item', wraps=freeze_item) as freeze, \
            patch('improvement_selection.all_corpus_items', side_effect=AssertionError('unrelated corpus read')):
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes=set(hashes)) == [hashes[key] for key in sorted(hashes)]
        assert freeze.call_count == 3


def test_stale_projection_falls_back_and_rejects_changed_input(archive):
    path, writer, items = archive
    original = next(item for item in items if item['source_url'] == 'https://example.org/3?id=1&part=2')
    wanted = freeze_item(original)['input_hash']
    post(writer, 3, '공공 AI 조건 변경\nhttps://example.org/3?id=1&part=2')
    with sqlite3.connect(path) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute('BEGIN')
        assert selected_documents(reader, {original['corpus_identity']}) is None
        assert corpus(reader, input_hashes={wanted}) == []
    current = all_corpus_items(writer)
    changed = next(item for item in current if item['source_url'] == original['source_url'])
    frozen = freeze_item(changed)
    writer.execute('INSERT INTO bulk_baseline_cache VALUES(?,?)', (frozen['input_hash'], frozen['document_id']))
    writer.commit()
    with sqlite3.connect(path) as reader, patch('improvement_selection.all_corpus_items', side_effect=AssertionError('full corpus')):
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes={wanted}) == []
        assert corpus(reader, input_hashes={frozen['input_hash']}) == [changed]


def test_identity_hint_cannot_admit_a_different_source_and_deletion_is_fresh(archive):
    path, writer, items = archive
    target, other = map(freeze_item, items[:2])
    writer.execute('UPDATE bulk_baseline_cache SET document_id=? WHERE input_hash=?', (other['document_id'], target['input_hash']))
    writer.commit()
    with sqlite3.connect(path) as reader:
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes={target['input_hash']}) == []
    writer.execute('DELETE FROM bulk_baseline_cache WHERE input_hash=?', (other['input_hash'],))
    writer.commit()
    with sqlite3.connect(path) as reader:
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes={other['input_hash']}) == []


def test_concurrent_projection_refresh_does_not_contaminate_pinned_reader(archive):
    path, writer, items = archive
    original = next(item for item in items if item['source_url'] == 'https://example.org/3?id=1&part=2')
    wanted = freeze_item(original)['input_hash']
    with sqlite3.connect(path) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes={wanted}) == [original]
        post(writer, 3, '수정된 원문 조건\nhttps://example.org/3?id=1&part=2')
        all_corpus_items(writer)
        assert selected_documents(reader, {original['corpus_identity']}) is None
        assert corpus(reader, input_hashes={wanted}) == [original]
    with sqlite3.connect(path) as reader:
        reader.execute('BEGIN')
        assert corpus(reader, input_hashes={wanted}) == []
