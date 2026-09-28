"""Reviewed paper-claim citations for GraphRAG, with current-metadata checks."""
import hashlib
import json
from projection_cache import cached_read, content_digest

from arxiv_papers import _paper_rows, _rows, parse_arxiv_id
from paper_analysis import analysis_input_hash, digest, validate_report


def validated_paper_analysis(row, item):
    if (row.get('status') != 'complete' or row.get('error') or item.get('metadata_status') != 'fetched'
            or not item.get('title') or not item.get('abstract') or row.get('input_hash') != analysis_input_hash(item)):
        return {}
    try:
        result = json.loads(row.get('result_json') or 'null')
    except (TypeError, ValueError):
        return {}
    if not isinstance(result, dict) or result.get('verified') is not True or result.get('paper_id') != item['paper_id']:
        return {}
    if result.get('input_hash') != row['input_hash'] or result.get('version') != item.get('metadata_version'):
        return {}
    report, audit, evidence = result.get('report'), result.get('verification'), result.get('evidence')
    if (not isinstance(audit, dict) or audit.get('accepted') is not True or audit.get('issues') != []
            or not isinstance(evidence, list) or not evidence or audit.get('report_hash') != digest(report)
            or audit.get('evidence_hash') != digest(evidence)):
        return {}
    ids = set()
    for source in evidence:
        if (not isinstance(source, dict) or not isinstance(source.get('id'), str) or not source['id']
                or source['id'] in ids or source.get('paper_id') != item['paper_id']
                or source.get('origin') not in {'arxiv_abstract', 'arxiv_metadata', 'arxiv_html_excerpt','scholarly_index_abstract','scholarly_index_metadata'}
                or not isinstance(source.get('text'), str) or not source['text'].strip()
                or source.get('status', 'fetched') in {'failed', 'blocked', 'needs_review'}):
            return {}
        url = source.get('source_url')
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
            return {}
        secondary=item.get('metadata_source') in {'openalex','semantic_scholar'}
        if source['origin'].startswith('scholarly_index_'):
            from urllib.parse import urlparse
            host={'openalex':'openalex.org','semantic_scholar':'www.semanticscholar.org'}.get(item.get('metadata_source'))
            if not secondary or source.get('metadata_source')!=item['metadata_source'] or url!=item.get('metadata_record_url') or urlparse(url).hostname!=host:return {}
            if source['origin']=='scholarly_index_abstract' and source['text']!=item['abstract'][:6000]:return {}
        else:
            if secondary:return {}
            parsed = parse_arxiv_id(url)
            if not parsed or parsed['paper_id'] != item['paper_id']:return {}
            if parsed['version'] is not None and parsed['version'] != item.get('metadata_version'):return {}
        if source.get('version') != item.get('metadata_version'):return {}
        ids.add(source['id'])
    checked = audit.get('checked_evidence_ids')
    if not isinstance(checked, list) or not all(isinstance(ref, str) and ref in ids for ref in checked):
        return {}
    try:
        validate_report(report, evidence, result.get('keywords') or [])
    except (ValueError, TypeError, KeyError):
        return {}
    refs = {ref for claim in report['claims'] for ref in claim['evidence_ids']}
    if not refs.issubset(checked):
        return {}
    return result


def _graph(result, item):
    evidence = []
    refs = {ref for claim in result['report']['claims'] for ref in claim['evidence_ids']}
    for source in result['evidence']:
        if source['id'] in refs:
            evidence.append(dict(source, published_at=source.get('published') or item.get('published'),
                                 topic=item.get('primary_category') or 'paper', topics=item.get('categories') or ['paper']))
    nodes = [{'id': 'source', 'name': 'https://arxiv.org/abs/'+item['paper_id'], 'type': 'SourceDocument',
              'summary': item['title'], 'evidence_ids': sorted(refs)}]
    edges = []
    for claim in result['report']['claims']:
        identity = 'paper-claim:'+hashlib.sha256((item['paper_id']+'\0'+claim['title']+'\0'+claim['detail']).encode()).hexdigest()[:24]
        meaning = claim['detail']+' / 불확실성: '+claim['uncertainty']
        nodes.append({'id': identity, 'name': item['paper_id']+' · '+claim['title'], 'type': 'PaperClaim',
                      'summary': meaning, 'evidence_ids': claim['evidence_ids']})
        edges.append({'source': identity, 'target': 'source', 'relation': '근거 인용', 'meaning': meaning,
                      'confidence': 'reviewed_interpretation', 'evidence_ids': claim['evidence_ids']})
    return {'nodes': nodes, 'edges': edges, 'evidence': evidence}


def paper_sources(db):
    items = _paper_rows(db)
    rows = {row['paper_id']: row for row in _rows(db, 'arxiv_paper_analyses')}
    coverage = dict.fromkeys(['total', 'metadata_fetched', 'verified', 'needs_review', 'stale', 'pending', 'failed', 'unassessed'], 0)
    coverage['total'] = len(items)
    sources = []
    for item in items:
        coverage['metadata_fetched'] += item['metadata_status'] == 'fetched'
        row = rows.get(item['paper_id'])
        if row is None:
            coverage['unassessed'] += 1
        elif row['input_hash'] != analysis_input_hash(item):
            coverage['stale'] += 1
        elif row['status'] in {'queued', 'running', 'paused'}:
            coverage['pending'] += 1
        elif row['status'] == 'failed':
            coverage['failed'] += 1
        else:
            result = cached_read(db, 'paper_verified_content', content_digest((row, analysis_input_hash(item),item.get('metadata_status'))),
                                 lambda: validated_paper_analysis(row, item), copy_result=False)
            if result:
                coverage['verified'] += 1
                sources.append({'kind': 'paper', 'id': item['paper_id'], 'result': _graph(result, item), 'row': row})
            else:
                coverage['needs_review'] += 1
    return sources, coverage


def paper_coverage(db):
    """Counts use the same current-source and independent-review gate as GraphRAG."""
    return paper_sources(db)[1]
