"""Deterministic review-cause routing with explicit bounded actions."""
import hashlib
import json
import re

RULES=[('source_fetch','원문 재수집',r'조회.*실패|원문.*실패|fetch|timeout.*source|HTTP|차단', 'source_enrichment', 'refresh_existing_url_once'),
       ('source_context','문맥 재연결',r'문맥|스냅샷.*일치|원문.*일치하지|source.*changed|제목.*다르','context_review','compare_snapshot_to_current_source'),
       ('date_unknown','기사 날짜 확인',r'날짜|발행|시각|horizon|시간 범위','date_review','inspect_explicit_article_date'),
       ('citation_missing','인용 대조',r'인용|근거 ID|근거.*누락|evidence.?id|독립 대조.*누락','citation_review','check_ids_and_document_coverage'),
       ('overstatement','주장·등급 보완',r'과장|단정|근거.*부족|등급|귀속|행위자|뒷받침|moderate|critical','semantic_review','revise_claim_then_independent_review'),
       ('execution','실행 복구',r'실행|시간.*초과|timeout|엔진|로그인','execution_review','resume_saved_checkpoint')]

def route_review(run):
    result=run.get('results') or {};evidence=result.get('evidence') or []
    ids={entry.get('id') for entry in evidence};issues=[]
    for key in ('verification','risk_verification'):
        issues.extend(str(issue) for issue in (result.get(key) or {}).get('issues',[]))
    if run.get('error'):issues.append(str(run['error']))
    for url in (result.get('coverage') or {}).get('failed_urls',[]):issues.append('원문 조회 실패: '+str(url))
    tasks=[]
    for issue in dict.fromkeys(issues):
        matches=[rule for rule in RULES if re.search(rule[2],issue,re.I)] or [('semantic_review','독립 재검토','', 'semantic_review','revise_claim_then_independent_review')]
        refs=sorted(entry['id'] for entry in evidence if isinstance(entry.get('id'),str) and (entry['id'] in issue or bool(entry.get('url') and entry['url'] in issue)))
        for kind,title,_,agent,action in matches:
            key=hashlib.sha256(json.dumps([run.get('id'),kind,issue],ensure_ascii=False).encode()).hexdigest()[:24]
            tasks.append({'id':key,'kind':kind,'title':title,'reason':issue,'evidence_ids':refs,'source_run_id':run.get('id'),
                'search_terms':list(dict.fromkeys(entry.get('title','') for entry in evidence if entry.get('id') in refs and entry.get('title')))[:6],
                'assigned_role':agent,'action':action,'max_attempts':1,'status':'open',
                'scope':'현재 선택 근거/이미 수집한 URL만. 새 사실을 만들거나 같은 자료를 무한 재실행하지 않음'})
    return tasks
