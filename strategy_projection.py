"""Incremental document preparation over the repository's canonical dedup view."""
from task_lifecycle import checkpoint
from keyword_index import keyword_record_id
from projection_store import document_rows, rules_digest

RULE_FILES = ('strategy_projection.py', 'strategy.py', 'morphology.py', 'url_context.py',
              'sector_taxonomy.py', 'strategic_value.py', 'keyword_index.py')


def prepare_documents(db, raw, mappings):
    from strategy import focused_items
    from morphology import keyword_records
    from sector_taxonomy import classify_sectors
    from strategic_value import evaluate_news
    from document_features import cached_features, read as read_features
    originals = {keyword_record_id(item): item for item in raw}
    rules = rules_digest(RULE_FILES)
    inputs = {rid: [rules, original, mappings.get(rid, [])] for rid, original in originals.items()}
    def build(missing):
        items = focused_items([originals[rid] for rid in missing])
        morph = keyword_records(db, items)
        output = {}
        with cached_features(db) as features:
            for rid, item in zip(missing, items):
                checkpoint()
                original = originals[rid]
                values = read_features(features, item) or {'sectors': classify_sectors(item), 'strategic_value': evaluate_news(item)}
                output[rid] = dict(item, **values, item_id=rid, strategic_keywords=morph.get(rid, []),
                    _search_text=original.get('text', '').casefold(),
                    _all_keyword_ids=[row['keyword_id'] for row in mappings.get(rid, [])])
        return output
    rows = document_rows(db, 'strategy-documents-v2', inputs, build)
    items = list(rows.values())
    return items, {rid: item['strategic_keywords'] for rid, item in rows.items()}
