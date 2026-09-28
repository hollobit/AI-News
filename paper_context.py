"""Use only current, independently reviewed paper findings in reader-facing views."""
import json
from paper_graph import paper_sources
from evidence_search import terms
from source_store import digest


def token(source):
    row=source['row']
    return digest(row['input_hash']+'\0'+(row.get('result_json') or ''))


def retrieve(db,query,limit=2):
    sources,coverage=paper_sources(db);query_terms=set(terms(query));ranked=[]
    for source in sources:
        graph=source['result'];text=' '.join(e.get('title','')+' '+e.get('text','') for e in graph['evidence'])
        overlap=query_terms&set(terms(text))
        # Require multiple shared terms; a generic single AI mention is insufficient.
        if len(overlap)>=2 or source['id'] in query:ranked.append((len(overlap),source))
    evidence=[];dependencies=[]
    for _,source in sorted(ranked,key=lambda x:(-x[0],x[1]['id']))[:limit]:
        result=json.loads(source['row']['result_json']);refs=set()
        claims=sorted(result['report']['claims'],key=lambda c:-len(query_terms&set(terms(c['title']+' '+c['detail']))))[:3]
        for claim in claims:refs.update(claim['evidence_ids'])
        for e in result['evidence']:
            if e['id'] in refs:evidence.append(dict(e,id='paper:'+source['id']+':'+e['id'],source_kind='reviewed_paper_evidence',verification='accepted_paper_analysis',content_hash=digest(e['text'])))
        for i,c in enumerate(claims):
            evidence.append({'id':'paper:'+source['id']+':claim:'+str(i),'title':result['evidence'][0]['title'],
                'text':c['title']+' — '+c['detail']+'\n조건/불확실성: '+c['uncertainty'],
                'source_url':'https://arxiv.org/abs/'+source['id'],'source_kind':'reviewed_paper_interpretation','verification':'accepted_paper_analysis','paper_id':source['id'],
                'scope':result.get('scope','abstract_and_official_metadata_only'),'supporting_evidence_ids':['paper:'+source['id']+':'+r for r in c['evidence_ids']]})
        dependencies.append({'paper_id':source['id'],'token':token(source)})
    return evidence,dependencies


def current(db,dependencies):
    if not dependencies:return True
    sources,_=paper_sources(db);tokens={s['id']:token(s) for s in sources}
    return all(tokens.get(p['paper_id'])==p['token'] for p in dependencies)


def strategic_context(db):
    from strategy_trends import LENSES,matched_terms
    sources,coverage=paper_sources(db);topics=[]
    for lens in LENSES:
        papers=[]
        for source in sources:
            result=json.loads(source['row']['result_json'])
            text=' '.join(e['text'] for e in result['evidence'] if e['origin'] in ('arxiv_abstract','scholarly_index_abstract'))
            title=result['evidence'][0]['title']
            if not matched_terms({'title':title,'text':text},lens['terms']):continue
            claims=[c for c in result['report']['claims'] if c['category'] in ('method','reported_result','strategic_implication')][:2]
            papers.append({'paper_id':source['id'],'title':title,'summary':result['report']['summary'],'scope':result.get('scope','abstract_and_official_metadata_only'),'claims':claims})
        if papers:topics.append({'id':lens['id'],'name':lens['name'],'count':len(papers),'papers':papers[:3]})
    return {'coverage':coverage,'topics':topics,'method':'현재 원문과 일치하고 독립 검토를 통과한 논문 분석입니다. 뉴스 관측 건수에 합산하지 않습니다.'}
