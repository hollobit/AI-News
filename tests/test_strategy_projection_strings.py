import json
import pickle
from strategy_views import _share_projection_strings


def test_compact_projection_preserves_values_aliases_and_input_isolation():
    rows = [json.loads('{"keyword":"frequent-strategy-keyword", "pos":["NNP"]}') for _ in range(500)]
    original = {'items': rows, 'morph': {'record': rows}}
    original_json = json.dumps(original, ensure_ascii=False)
    compact = _share_projection_strings(original)
    assert json.dumps(compact, ensure_ascii=False) == original_json
    assert compact['morph']['record'] is compact['items']
    assert len(pickle.dumps(compact)) < len(pickle.dumps(original)) / 2
    compact['items'][0]['pos'].append('TEST')
    assert json.dumps(original, ensure_ascii=False) == original_json
    assert compact['items'][1]['pos'] == ['NNP']
