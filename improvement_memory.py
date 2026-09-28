"""Versioned strategic review memory; proposals remain proposals, never new facts."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone

from graph_rag import validated_workflow_content
from evidence_corrections import project_catalog
from morphology import extract_keywords


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _identity(kind, label):
    text = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', label)).strip().casefold()
    return kind + ':' + _digest(text)[:24]


def init_memory(db):
    db.execute('CREATE TABLE IF NOT EXISTS improvement_catalog (id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload_json TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS improvement_catalog_history (
        id TEXT NOT NULL, version INTEGER NOT NULL, run_id TEXT NOT NULL, created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL, PRIMARY KEY(id,version))''')
    db.execute('CREATE TABLE IF NOT EXISTS improvement_ingestions (run_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, result_json TEXT NOT NULL)')


def list_catalog(db, limit=100):
    init_memory(db)
    rows = db.execute('SELECT payload_json FROM improvement_catalog ORDER BY id LIMIT ?',
                      (max(1, min(500, int(limit))),)).fetchall()
    return {'items': project_catalog(db, [json.loads(row[0]) for row in rows]),
            'version': db.execute('SELECT COUNT(*) FROM improvement_catalog_history').fetchone()[0],
            'limitations': ['관측 키워드, 제안 전략 개념, 검증된 해석의 인용 관계를 구분합니다.',
                            '카탈로그는 검토 기억이며 새로운 사실 근거·인과관계·자동 점수 상승의 근거가 아닙니다.']}


def catalog_history(db, entry_id):
    init_memory(db)
    return [dict(json.loads(row[0]), recorded_at=row[1], source_run_id=row[2]) for row in db.execute(
        'SELECT payload_json,created_at,run_id FROM improvement_catalog_history WHERE id=? ORDER BY version', (entry_id,))]


def _source_keywords(payload):
    results = {}
    for item in payload.get('evidence') or []:
        if (not isinstance(item, dict) or item.get('origin') not in {'telegram_excerpt', 'fetched_url_excerpt'}
                or not isinstance(item.get('id'), str) or not isinstance(item.get('text'), str)
                or item.get('status', 'fetched') in {'failed', 'blocked', 'needs_review'}):
            continue
        results[item['id']] = [term for term in extract_keywords(item['text']) if term['kind'] != 'noun'][:40]
    return results


def improve_catalog(db, workflow_run_id, workflow):
    """Ingest a WorkflowService.get_run result using a dedicated connection.

    Call before starting caller-owned writes: morphology is performed before this
    function opens its write transaction. Follow-up suggestions never enact rules.
    """
    init_memory(db)
    payload = workflow.get('results') if isinstance(workflow.get('results'), dict) else {}
    fingerprint = _digest(workflow)
    previous = db.execute('SELECT input_hash,result_json FROM improvement_ingestions WHERE run_id=?', (workflow_run_id,)).fetchone()
    if previous and previous[0] == fingerprint:
        return dict(json.loads(previous[1]), added=[], updated=[])
    content = (validated_workflow_content(payload, workflow_run_id)
               if workflow.get('id') == workflow_run_id and workflow.get('status') == 'complete' and not workflow.get('error') else {})
    keywords = _source_keywords(payload)
    def search_terms(refs=None):
        selected = refs if refs is not None else keywords
        return list(dict.fromkeys(term['label'] for ref in selected for term in keywords.get(ref, [])))[:6]

    followups, rules = [], []
    def task(kind, title, reason, refs=None):
        followups.append({'id': _identity(kind, reason), 'kind': kind, 'title': title, 'reason': reason,
                          'evidence_ids': list(refs or []), 'source_run_id': workflow_run_id,
                          'search_terms': search_terms(refs)})
    audit = payload.get('verification') or {}
    issues = audit.get('issues') if isinstance(audit, dict) else []
    for issue in (issues or [])[:4]:
        if not isinstance(issue, str) or not issue.strip():
            continue
        reason = issue.strip()[:500]
        task('review_evidence', '미해결 검증 지적 보완', reason)
        rules.append({'id': _identity('review_rule', reason), 'title': '다음 회차 검증 항목 보완',
                      'reason': reason, 'status': 'proposed', 'source_run_id': workflow_run_id,
                      'effect': '검증자가 재검토할 항목 제안; 채점 기준·점수·승인 임계값은 변경하지 않음'})
    risk_audit = payload.get('risk_verification') or {}
    for issue in (risk_audit.get('issues') or [])[:2]:
        if isinstance(issue, str) and issue.strip():
            task('review_risk', '현재 위협·미래 시나리오 검증 보완', issue.strip()[:500])
    coverage = payload.get('coverage') or workflow.get('coverage') or {}
    for url in (coverage.get('failed_urls') or [])[:2]:
        task('retrieve_source', '누락 원문 보강', '원문 조회 실패: ' + str(url)[:400])
    if not content and not followups:
        task('review_evidence', '결과 검증 재확인', '완료·승인·보고서/원문 해시 또는 인용 근거 검증을 통과하지 못했습니다.')

    pending = {}
    if content:
        global_map = content['evidence_map']
        evidence = {item['id']: item for item in content['evidence']}
        def entry(kind, label, refs, **fields):
            item_id = fields.pop('id', _identity(kind, label))
            global_refs = sorted({global_map[ref] for ref in refs if ref in global_map})
            if not global_refs:
                return
            value = pending.setdefault(item_id, {'id': item_id, 'kind': kind, 'label': label,
                                                'run_ids': [workflow_run_id], 'evidence_ids': [],
                                                'evidence': [], **fields})
            value['evidence_ids'] = sorted(set(value['evidence_ids']) | set(global_refs))
            value['evidence'] = [evidence[ref] for ref in value['evidence_ids']]
        for raw_id in global_map:
            for term in keywords.get(raw_id, []):
                entry('observed_keyword', term['label'], [raw_id], id='observed:'+term['id'],
                      epistemic_status='observed_in_sources', detail='원문에서 형태소 분석으로 관측한 단어·구',
                      morphology_kind=term['kind'], pos=term['pos'], surface=term['surface'])
        for claim in content['claims']:
            refs = claim['evidence_ids']
            entry('claim_relation', claim['title'], refs, id='relation:'+claim['claim_id'],
                  epistemic_status='reviewed_interpretation', detail=claim['detail'], uncertainty=claim['uncertainty'],
                  relation='근거 인용', category=claim.get('category', ''),
                  caveat='검증된 전략 해석의 출처 연결; 사실·인과관계의 확정이 아님')
            if claim.get('category') == 'strategic_concept':
                entry('strategic_concept', claim['title'], refs, epistemic_status='proposed',
                      detail=claim['detail'], uncertainty=claim['uncertainty'])
                task('validate_concept', '제안 개념의 추가 근거 탐색', claim['uncertainty'][:500], refs)
    added, updated = [], []
    with db:
        for item_id, value in pending.items():
            old = db.execute('SELECT version,payload_json FROM improvement_catalog WHERE id=?', (item_id,)).fetchone()
            if old:
                prior = json.loads(old[1])
                value['run_ids'] = sorted(set(value['run_ids']) | set(prior['run_ids']))
                value['evidence_ids'] = sorted(set(value['evidence_ids']) | set(prior['evidence_ids']))
                merged = {}
                for evidence in prior['evidence'] + value['evidence']:
                    previous_evidence = merged.get(evidence['id'], {})
                    merged[evidence['id']] = dict(evidence, workflow_run_ids=sorted(
                        set(previous_evidence.get('workflow_run_ids', [])) | set(evidence.get('workflow_run_ids', []))))
                value['evidence'] = [merged[ref] for ref in value['evidence_ids']]
            value['document_ids'] = sorted({e['document_id'] for e in value['evidence']})
            value['support_count'] = len(value['document_ids'])
            if old and value == {key: item for key, item in prior.items() if key != 'version'}:
                continue
            value['version'] = old[0] + 1 if old else 1
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT OR REPLACE INTO improvement_catalog VALUES (?,?,?)', (item_id, value['version'], serialized))
            db.execute('INSERT INTO improvement_catalog_history VALUES (?,?,?,?,?)',
                       (item_id, value['version'], workflow_run_id, datetime.now(timezone.utc).isoformat(), serialized))
            (updated if old else added).append(value)
        result = {'accepted': bool(content), 'added': added, 'updated': updated,
                  'followup_tasks': followups[:6], 'rule_proposals': rules[:4],
                  'catalog_version': db.execute('SELECT COUNT(*) FROM improvement_catalog_history').fetchone()[0]}
        db.execute('INSERT OR REPLACE INTO improvement_ingestions VALUES (?,?,?)',
                   (workflow_run_id, fingerprint, json.dumps(result, ensure_ascii=False)))
    return result
