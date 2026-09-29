"""Reviewed baseline summaries and observed keywords, linked only to source excerpts."""
from task_lifecycle import checkpoint
from verified_cache import scoped as verification_scope
import json
from projection_cache import cached_read, revision_token, content_digest

from bulk_baseline import BulkBaselineService, freeze_item, validate_record, RECORD


@verification_scope
def baseline_sources(db, items=None):
    coverage = {'baseline_cached_analyses': 0, 'completed_verified_baselines': 0,
                'baseline_stale_or_invalid': 0}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='bulk_baseline_cache'").fetchone():
        return [], coverage
    def snapshots():
        if items is None:
            from improvement_selection import all_corpus_items
            selected = all_corpus_items(db)
        else:
            selected = items
        return {s['input_hash']: s for s in map(freeze_item, selected)}
    current = cached_read(db, 'baseline_current_snapshots', revision_token(db, ('source',)), snapshots,
                          copy_result=False) if items is None else snapshots()
    sources = []
    for row in db.execute('SELECT input_hash,result_json,prepared_json FROM bulk_baseline_cache'):
        checkpoint()
        coverage['baseline_cached_analyses'] += 1
        snapshot = current.get(row[0])
        if snapshot is None:
            coverage['baseline_stale_or_invalid'] += 1
            continue
        def validate():
            try:
                result, prepared = json.loads(row[1]), json.loads(row[2])
                if result.get('input_hash') != row[0] or not BulkBaselineService._valid_cached(result, snapshot):
                    return None
                record = {k: result.get(k) for k in RECORD['required']}
                validate_record(record, snapshot, prepared)
                if result.get('evidence') != snapshot['evidence']:
                    return None
                return result
            except (ValueError, TypeError, KeyError, AttributeError):
                return None
        result = cached_read(db, 'baseline_verified_content', content_digest((row[0],row[1],row[2])),
                             validate, copy_result=False)
        if not result:
            coverage['baseline_stale_or_invalid'] += 1
            continue
        refs = result['evidence_ids']
        evidence = [dict(e, source_url=e.get('url', '')) for e in snapshot['evidence'] if e['id'] in refs]
        if any(e.get('origin') not in {'telegram_excerpt', 'fetched_url_excerpt'} for e in evidence):
            coverage['baseline_stale_or_invalid'] += 1
            continue
        nodes = [dict(id='source', name=snapshot['document_id'], type='SourceDocument',
                      summary=snapshot['title'], evidence_ids=refs),
                 dict(id='summary', name=result['summary'], type='NewsSummary',
                      summary=result['summary']+' / 분석 한계: '+result['limitations'], evidence_ids=refs)]
        edges = [dict(source='summary', target='source', relation='근거 인용',
                      meaning='독립 검토를 통과한 발췌 요약; 외부 사실 확인이나 전체 본문 분석을 뜻하지 않음',
                      confidence='reviewed_interpretation', evidence_ids=refs)]
        for index, keyword in enumerate(result['keywords']):
            checkpoint()
            supported = [e['id'] for e in evidence if keyword['source_quote'] in e['text']]
            if not supported:
                continue
            identity = 'keyword:'+str(index)
            nodes.append(dict(id=identity, name=keyword['label'], type='Keyword',
                              summary='원문 관측 표현: '+keyword['source_quote'], evidence_ids=supported))
            edges.append(dict(source=identity, target='source', relation='원문 표현 관측',
                              meaning=keyword['source_quote'], confidence='observed_in_excerpt', evidence_ids=supported))
        sources.append(dict(kind='baseline', id=row[0], row={}, result=dict(nodes=nodes, edges=edges, evidence=evidence)))
        coverage['completed_verified_baselines'] += 1
    return sources, coverage
