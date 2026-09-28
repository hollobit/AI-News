"""Evidence-linked research progress; publication alone never implies adoption."""
import json
import math
import re
from collections import Counter
from arxiv_papers import _paper_rows, _rows, parse_arxiv_id
from paper_graph import validated_paper_analysis
from strategic_records import save_record, list_records, validate_refs, text, evidence_state

STAGES=['publication','replication','benchmark','implementation','pilot','deployment','procurement','standard']
LABELS=dict(zip(STAGES,['논문 발표','재현 연구','성능 비교','구현 공개','현장 실증','실제 도입','조달','표준']))


def stage_observations(db,identity):
    patterns={'replication':r'재현|replicat\w*|reproduc\w*|复现',
        'benchmark':r'벤치마크|benchmark|基准测试','implementation':r'코드 공개|소스 공개|open.source|code release|开源',
        'pilot':r'현장 실증|시범 운영|field trial|pilot|试点','deployment':r'실제 도입|상용화|deployed|production deployment|落地',
        'procurement':r'조달|정부 계약|procurement|采购','standard':r'표준 채택|standardiz\w*|标准'}
    result=[]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='intel_documents'").fetchone():return result
    for row in db.execute('SELECT payload_json FROM intel_documents WHERE instr(payload_json,?)>0 LIMIT 50',(identity,)):
        doc=json.loads(row[0])
        if doc.get('status')!='current':continue
        body='\n'.join(str(doc.get(k) or '') for k in ('title','text','source_text'))
        at=body.find(identity)
        snippet=body[max(0,at-350):at+500] if at>=0 else body[:600]
        for stage,pattern in patterns.items():
            if re.search(pattern,snippet,re.I):
                result.append({'stage':stage,'label':LABELS[stage],'status':'reported_mention','evidence_ids':[doc['id']],
                    'quote':snippet,'source_url':doc.get('source_url'),'document_version':doc.get('version'),
                    'basis':'해당 논문과 도입 단계 표현의 가까운 문맥 관측; 재현 성공·실제 도입의 확인 아님'})
    return result


def milestone(db,payload,document_lookup):
    parsed=parse_arxiv_id(payload.get('paper_id'))
    if not parsed or not any(p['paper_id']==parsed['paper_id'] for p in _paper_rows(db)):
        raise ValueError('보관 중인 논문 ID를 선택해 주세요.')
    stage=payload.get('stage')
    if stage not in STAGES[1:]:raise ValueError('연구·도입 단계를 확인해 주세요.')
    refs=validate_refs(payload.get('evidence_ids'),document_lookup)
    if not refs:raise ValueError('해당 단계의 근거 문서가 필요합니다.')
    note=text(payload.get('note',''),'연구 연결 근거',True,3000)
    return save_record(db,'milestone',{'title':LABELS[stage]+' · '+parsed['paper_id'],
        'paper_id':parsed['paper_id'],'stage':stage,'note':note,'evidence_ids':refs,
        'evidence_state':evidence_state(refs,document_lookup),'status':'user_linked',
        'basis':'사용자가 연결한 도입 근거; 인과관계와 성과는 별도 검토 필요'},'연구·도입 근거 연결')


def research_view(db,params,document_lookup):
    def one(key,default=''):
        value=params.get(key,default);return value[0] if isinstance(value,list) and value else value
    query=str(one('q')).casefold();identity=str(one('id'))
    page=max(1,int(one('page',1)));size=min(50,max(1,int(one('page_size',12))))
    rows={row['paper_id']:row for row in _rows(db,'arxiv_paper_analyses')}
    milestones=[json.loads(row[0]) for row in db.execute("SELECT payload_json FROM intel_records WHERE kind='milestone'")]
    items=[]
    for paper in _paper_rows(db):
        if identity and paper['paper_id']!=identity:continue
        if query and query not in (paper['title']+' '+paper['paper_id']+' '+paper['abstract']).casefold():continue
        result=validated_paper_analysis(rows.get(paper['paper_id'],{}),paper)
        reports=result.get('report',{}).get('claims',[])
        linked=[]
        for entry in milestones:
            if entry['paper_id']!=paper['paper_id']:continue
            state=evidence_state(entry['evidence_ids'],document_lookup)
            linked.append(dict(entry,status='stale' if state['fingerprint']!=entry['evidence_state']['fingerprint'] else entry['status']))
        observations=stage_observations(db,paper['paper_id']) if identity else []
        stages=[]
        for stage in STAGES:
            links=[m for m in linked if m['stage']==stage]
            evidence=list(dict.fromkeys(ref for m in links for ref in m['evidence_ids']))
            stages.append({'stage':stage,'label':LABELS[stage],
                'status':('observed' if paper['metadata_status']=='fetched' else 'metadata_pending') if stage=='publication'
                else ('needs_review' if any(m['status']=='stale' for m in links) else 'user_linked') if links else 'unknown',
                'evidence_ids':evidence,'links':links,'observations':[o for o in observations if o['stage']==stage]})
        items.append({'id':paper['paper_id'],'paper_id':paper['paper_id'],'title':paper['title'],
            'summary':result.get('report',{}).get('summary') or paper['abstract'][:500],
            'source_url':paper['source_url'],'status':'verified' if result else paper['analysis_status'],
            'metadata_status':paper['metadata_status'],'stages':stages,'application_domains':paper.get('sectors',[]),
            'reported_results':[c for c in reports if c['category'] in ('reported_result','comparison')],
            'reproducibility':[c for c in reports if c['category']=='reproducibility'],
            'limitations':result.get('report',{}).get('limitations',[]),
            'strategic_implications':[c for c in reports if c['category']=='strategic_implication'],
            'evidence_count':len(result.get('evidence',[])),'evidence':result.get('evidence',[]),
            'updated_at':paper.get('updated') or paper.get('fetched_at'),
            'basis':'논문 결과는 저자의 보고와 검토된 해석; 산업 도입 단계는 별도 근거로 연결'})
    if identity:return {'item':items[0] if items else None,'history':[m for m in milestones if m['paper_id']==identity] if items else []}
    total=len(items)
    return {'items':items[(page-1)*size:page*size],'total':total,'page':page,'page_size':size,
            'total_pages':max(1,math.ceil(total/size)),
            'coverage':{'metadata':dict(Counter(i['metadata_status'] for i in items)),
                        'analysis':dict(Counter(i['status'] for i in items)),
                        'scope':'수집 자료에 연결된 논문과 도입 근거; 전체 연구 시장 통계가 아님'}}
