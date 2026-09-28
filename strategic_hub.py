"""Application boundary for source-to-decision intelligence and incremental refresh."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import json
import math
import sqlite3
import threading

from strategic_store import init_store, sync_store, query_store, mutate_store, get_document
from strategic_records import (init_records, now, digest, get_record, list_records, record_history,
    save_record, decision_value, scenario_value, refresh_record, text, strings)
from strategic_assessments import (init_assessments, sync_assessments, score_document, profile_value,
    default_profile, risk_timeline, apply_profile)
from strategic_jobs import StrategicJobs
from strategic_research import research_view, milestone


def one(params,key,default=''):
    value=params.get(key,default)
    return value[0] if isinstance(value,list) and value else value


def entities(db,kind):
    return [json.loads(r[0]) for r in db.execute('SELECT payload_json FROM intel_entities WHERE kind=?',(kind,))]


def paginate(items,params):
    page=max(1,int(one(params,'page',1)));size=min(50,max(1,int(one(params,'page_size',12))))
    total=len(items);pages=max(1,math.ceil(total/size));page=min(page,pages)
    return {'items':items[(page-1)*size:page*size],'total':total,'page':page,'page_size':size,'total_pages':pages}


def collection_scores(rows,profile):
    scores={key:round(sum(values)/len(values),1) if (values:=[r['scores'][key] for r in rows if r['scores'][key] is not None]) else None
            for key in ('importance','risk','evidence','opportunity')}
    risks=[r['scores']['risk'] for r in rows]
    scores['risk']=max(risks) if risks and all(v is not None for v in risks) else None
    ranges={key:[v,v] if v is not None else [0,100] for key,v in scores.items()}
    ranges['risk']=[max((r['score_details']['risk']['range'][i] for r in rows),default=100*i) for i in (0,1)]
    weighted=[round(sum(profile['weights'][key]*ranges[key][i] for key in scores),2) for i in (0,1)]
    return scores,{'score':weighted[0] if weighted[0]==weighted[1] else None,'range':weighted,
        'profile_id':profile['id'],'profile_version':profile['version']}


class StrategicHub:
    def __init__(self,path,simulation=None,analyzer=None,enabled=None,start_worker=True):
        self.path=str(path);self.simulation=simulation;self.lock=threading.RLock()
        self.stop=threading.Event();self.wake=threading.Event();self.last_token=None
        self.runtime={'status':'preparing','last_success':None,'error':None}
        with self.db() as db:
            init_store(db);init_records(db);init_assessments(db)
            db.execute('CREATE TABLE IF NOT EXISTS intel_runtime(key TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
        from rule_experiments import RuleExperimentService
        self.experiments=RuleExperimentService(path,enabled=enabled)
        self.jobs=StrategicJobs(path,analyzer,enabled,self._current_evidence)
        self.worker=None
        if start_worker:
            self.worker=threading.Thread(target=self._loop,daemon=True,name='strategic-intelligence-refresh')
            self.worker.start()

    def db(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row;return db

    def _token(self,db):
        from evidence_corrections import correction_token
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        revision=list(map(tuple,db.execute('SELECT * FROM projection_revisions'))) if 'projection_revisions' in tables else None
        operations=db.execute("SELECT COALESCE(max(seq),0) FROM intel_history WHERE id IN (SELECT id FROM intel_entities WHERE kind='operation')").fetchone()[0]
        return digest([revision,correction_token(db),operations,date.today().isoformat()]) if revision else None

    def _loop(self):
        while not self.stop.is_set():
            try:self.sync()
            except Exception as error:
                self.runtime.update(status='retrying',error=type(error).__name__+' — 전략 자료 갱신을 다시 시도합니다.')
            self.wake.wait(60);self.wake.clear()

    def sync(self,items=None,force=False):
        with self.lock,self.db() as db:
            token=self._token(db)
            if items is None and not force and token and token==self.last_token:
                self._refresh_records(db)
                return dict(self.runtime)
            self.runtime.update(status='updating',error=None)
            result=sync_store(db,items)
            documents=entities(db,'document');claims=entities(db,'claim')
            from risk_views import _all_risks
            risks=_all_risks(db)['risks']
            result['changed_assessments']=sync_assessments(db,documents,claims,risks)
            result['changed_risk_observations']=risk_timeline(db,risks)
            self._refresh_records(db)
            self.runtime.update(status='ready',last_success=now(),error=None,changes=result)
            db.execute('INSERT OR REPLACE INTO intel_runtime VALUES (?,?)',('status',json.dumps(self.runtime,ensure_ascii=False)))
            self.last_token=token
            return dict(self.runtime)

    def _refresh_records(self,db):
        docs={d['id']:d for d in entities(db,'document')};topics={t['id']:t for t in entities(db,'topic')}
        for row in db.execute("SELECT payload_json FROM intel_records WHERE kind IN ('decision','scenario')").fetchall():
            item=json.loads(row[0]);topic=topics.get(item.get('topic_id'),{})
            refresh_record(db,item,docs.get,topic.get('document_ids',[]))

    def _current_evidence(self,ids):
        with self.db() as db:return [doc for identity in ids if (doc:=get_document(db,identity)) and doc.get('status')=='current']

    def _assess(self,db,item,profile_id=None):
        ids=item.get('document_ids') or ([item['id']] if item.get('kind')=='document' else [])
        if not ids:return item
        rows=[]
        profile=None
        if profile_id and profile_id!='default':
            profile=get_record(db,profile_id,'profile')
            if not profile:raise ValueError('평가 프로필을 찾을 수 없습니다.')
        for index in range(0,len(ids),500):
            batch=ids[index:index+500]
            rows.extend(json.loads(r[0]) for r in db.execute('SELECT payload_json FROM intel_assessments WHERE document_id IN ('+','.join('?' for _ in batch)+')',batch))
        if profile:rows=[apply_profile(row,profile) for row in rows]
        if not rows:return item
        item=dict(item,risk_ids=list(dict.fromkeys(ref for row in rows for ref in row.get('score_details',{}).get('risk',{}).get('risk_ids',[]))))
        if len(rows)==1:return dict(item,**{k:rows[0][k] for k in ('scores','score_details','priority','opportunities','policy_observations','countries','domains')})
        scores,priority=collection_scores(rows,profile or default_profile())
        return dict(item,scores=scores,priority=priority,score_details={'basis':'중요도·근거 충족도는 문서 평균, 기회는 검토된 기회 문서 평균, 위험은 최고 수준; 미평가 위험 유지',
            'documents':len(ids),'assessed_documents':len(rows),'opportunity_documents':sum(r['scores']['opportunity'] is not None for r in rows),'profile_id':profile_id or 'default'},
            opportunities=[o for r in rows for o in r['opportunities']][:12])

    def _detail(self,db,view,identity,params):
        item=query_store(db,view,{'id':identity})
        if not item:return {'item':None}
        result={'item':self._assess(db,item,one(params,'profile_id'))}
        history=query_store(db,'history',{'id':identity,'page_size':100})
        result['history']=history['items']
        docs=[get_document(db,ref) for ref in item.get('document_ids',[])]
        docs=[d for d in docs if d]
        result['evidence']=[{k:d.get(k) for k in ('id','title','url','source_url','text','published_day','source_status')} for d in docs[:40]]
        result['evidence_total']=len(docs)
        claims=[]
        for ref in item.get('claim_ids',[]):
            claim=query_store(db,'claim',{'id':ref})
            if claim:claims.append(claim)
        result['item']['claims']=claims
        if view=='topic':result['item']['momentum']=query_store(db,'momentum',{'topic':identity})
        result['item']['source_count']=len({d['original_source_url'] for d in docs if d.get('original_source_url')})
        result['item']['source_count_unknown']=sum(not d.get('original_source_url') for d in docs)
        result['item']['disagreements']=self._disagreements(db,claims)
        return result

    @staticmethod
    def _disagreements(db,claims):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_artifacts'").fetchone():return []
        output=[]
        for run in dict.fromkeys(c.get('workflow_run_id') for c in claims if c.get('workflow_run_id')):
            row=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(run,)).fetchone()
            if not row:continue
            payload=json.loads(row[0]);deliberation=payload.get('deliberation')
            if deliberation:output.append({'workflow_run_id':run,'positions':deliberation.get('positions',[]),
                'checks':(payload.get('verification') or {}).get('deliberation_checks',[]),'basis':deliberation.get('basis')})
        return output

    def get(self,view='overview',params=None):
        params=params or {};identity=str(one(params,'id'))
        if view=='job':return {'item':self.jobs.get(identity)}
        if view=='experiments':return self._experiments_view(identity,params)
        with self.db() as db:
            if view in ('document','claim','event','topic','concept'):
                return self._detail(db,view,identity,params)
            if view=='overview':return self._overview(db,params)
            if view in ('documents','claims','events','topics','concepts'):
                if identity:return self._detail(db,view[:-1],identity,params)
                result=self._ranked_store(db,view,params) if view in ('events','topics') else query_store(db,view,params)
                result['items']=[self._assess(db,item,one(params,'profile_id')) for item in result['items']]
                for item in result['items']:
                    for key in ('text','source_text','snapshot_text','expressions','source_versions','claim_states','event_versions'):item.pop(key,None)
                result['runtime']=dict(self.runtime)
                return result
            if view in ('decision','scenario','decisions','scenarios','profiles'):
                kind={'decisions':'decision','scenarios':'scenario','profiles':'profile'}.get(view,view)
                if identity:
                    item=get_record(db,identity,kind)
                    result={'item':item,'history':record_history(db,identity)}
                    if item:result['evidence']=self._current_evidence(item.get('evidence_ids',[]))
                    if kind=='scenario':result['runs']=self.jobs.list(identity)
                    return result
                result=list_records(db,kind,params)
                if kind=='profile':result['default']=default_profile()
                return result
            if view=='research':return research_view(db,params,lambda ref:get_document(db,ref))
            if view=='operations':return self._operations(db,params)
            if view=='momentum':return query_store(db,view,params)
            if view=='coverage':return dict(query_store(db,view,params),runtime=self.runtime)
            if view=='risk_history':
                rows=[dict(json.loads(r[0]),change_cause=r[1],observed_at=r[2]) for r in db.execute(
                    'SELECT payload_json,cause,created_at FROM intel_risk_history ORDER BY created_at DESC')]
                if identity:
                    rows=[r for r in rows if r.get('id')==identity or r.get('identity')==identity]
                    return {'item':rows[0] if rows else None,'history':rows}
                return paginate(rows,params)
        raise ValueError('지원하지 않는 전략 화면입니다.')

    def _ranked_store(self,db,view,params):
        values=entities(db,view[:-1]);query=str(one(params,'q')).casefold()
        excluded={r[0] for r in db.execute('SELECT id FROM intel_exclusions WHERE excluded=1')}
        scores={r[0]:json.loads(r[1]) for r in db.execute('SELECT document_id,payload_json FROM intel_assessments')}
        profile_id=one(params,'profile_id')
        profile=default_profile()
        if profile_id and profile_id!='default':
            profile=get_record(db,profile_id,'profile')
            if not profile:raise ValueError('평가 프로필을 찾을 수 없습니다.')
            scores={key:apply_profile(row,profile) for key,row in scores.items()}
        values=[v for v in values if v.get('status')!='dormant' and v['id'] not in excluded and v.get('concept_id') not in excluded
                and (not query or query in (v.get('title') or v.get('label','')).casefold())]
        values.sort(key=lambda v:(collection_scores([scores[d] for d in v.get('document_ids',[]) if d in scores],profile)[1]['range'][0],
                                   len(v.get('claim_ids',[])),v['id']),reverse=True)
        days=int(one(params,'window_days',28))
        if days not in (7,28,90):raise ValueError('관측 기간은 7·28·90일 중 선택해 주세요.')
        documents={d['id']:d for d in entities(db,'document') if d['status']=='current'}
        end=date.today();start=(end-timedelta(days=days-1)).isoformat();before=(end-timedelta(days=2*days-1)).isoformat()
        current_all={k for k,d in documents.items() if start<=d.get('published_day','')<=end.isoformat()}
        previous_all={k for k,d in documents.items() if before<=d.get('published_day','')<start}
        for value in values:
            value.update(document_count=len(value.get('document_ids',[])),event_count=len(value.get('event_ids',[])),
                         evidence_count=len(value.get('claim_ids',[])),summary=value.get('basis',''))
            refs=set(value.get('document_ids',[]));current=refs&current_all;previous=refs&previous_all
            delta=((len(current)/len(current_all) if current_all else 0)-(len(previous)/len(previous_all) if previous_all else 0))*100
            channels=lambda ids:{documents[k]['channel'] for k in ids if documents[k].get('channel')}
            low=min(len(current),len(previous))<5;changed=channels(current)!=channels(previous)
            active_days=len({documents[k]['published_day'] for k in current})
            value['momentum']={'days':days,'current':len(current),'previous':len(previous),'share_change_pp':round(delta,2),
                'active_days':active_days,'low_sample':low,'coverage_changed':changed,
                'undated_documents':sum(not documents[k].get('published_day') for k in refs if k in documents),
                'status':'insufficient_evidence' if low or changed else 'growing' if delta>0 else 'cooling' if delta<0 else 'stable',
                'basis':'기간별 전체 문서량 보정; 채널 범위 차이와 저표본은 해석 보류'}
        return paginate(values,params)

    def _overview(self,db,params):
        coverage=query_store(db,'coverage');counts=dict(db.execute('SELECT kind,count(*) FROM intel_entities GROUP BY kind'))
        reviews=[json.loads(r[0]) for r in db.execute("SELECT payload_json FROM intel_records WHERE kind IN ('decision','scenario') ORDER BY updated_at DESC")]
        changes=[dict(item,resource=item['kind']+'s',href='/intelligence/'+item['kind']+'s?id='+item['id']) for item in reviews if item.get('review_state')=='needs_review']
        events=entities(db,'event');events=[e for e in events if e.get('status')!='dormant']
        scored=[]
        assessments={r[0]:json.loads(r[1]) for r in db.execute('SELECT document_id,payload_json FROM intel_assessments')}
        profile_id=one(params,'profile_id')
        if profile_id and profile_id!='default':
            profile=get_record(db,profile_id,'profile')
            if not profile:raise ValueError('평가 프로필을 찾을 수 없습니다.')
            assessments={key:apply_profile(value,profile) for key,value in assessments.items()}
        for event in events:
            weights=[assessments[d]['priority']['range'][0] for d in event['document_ids'] if d in assessments]
            scored.append((max(weights,default=0),event))
        scored.sort(key=lambda pair:(pair[0],pair[1]['id']),reverse=True)
        for _,event in scored[:8-len(changes[:8])]:
            changes.append(dict(self._assess(db,event,one(params,'profile_id')),resource='events',href='/intelligence/events?id='+event['id']))
        return {'items':changes[:8],'changes':changes[:8],'total':len(changes[:8]),'page':1,'page_size':8,'total_pages':1,
            'metrics':{'documents':coverage['current'],'events':len(events),'topics':db.execute("SELECT count(*) FROM intel_entities WHERE kind='topic' AND json_extract(payload_json,'$.status')!='dormant'").fetchone()[0],
                'current_claims':db.execute("SELECT count(*) FROM intel_entities WHERE kind='claim' AND json_extract(payload_json,'$.status')='current_reviewed'").fetchone()[0],
                'decisions':sum(i['kind']=='decision' for i in reviews),'review_needed':sum(i.get('review_state')=='needs_review' for i in reviews)},
            'coverage':coverage,'runtime':dict(self.runtime),'basis':'보관 자료의 검토 우선순위와 재검토할 결정'}

    def _operations(self,db,params):
        from review_routing import route_review
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")};items=[]
        if {'strategic_workflow_runs','strategic_workflow_artifacts'}<=tables:
            for row in db.execute("SELECT r.id,r.status,r.error,a.payload_json FROM strategic_workflow_runs r LEFT JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final' WHERE r.status IN ('needs_review','failed') ORDER BY r.updated_at DESC"):
                run={'id':row[0],'status':row[1],'error':row[2],'results':json.loads(row[3] or '{}')}
                items.extend(route_review(run))
            # Old accepted artifacts remain in history, but a corrected validator
            # can identify an explanation damaged by an earlier response schema.
            from risk_analysis import validate_risk_report
            for row in db.execute("SELECT r.id,a.payload_json FROM strategic_workflow_runs r JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final' WHERE r.status='complete' AND json_extract(a.payload_json,'$.risk_verified')=1"):
                result=json.loads(row[1])
                try:validate_risk_report(result.get('risk_report'),result.get('evidence') or [])
                except (ValueError,TypeError,KeyError) as error:
                    items.extend(route_review({'id':row[0],'status':'needs_review','error':str(error),'results':result}))
        query=str(one(params,'q')).casefold()
        if query:items=[i for i in items if query in json.dumps(i,ensure_ascii=False).casefold()]
        result=paginate(items,params)
        result.update(coverage={'causes':dict(Counter(i['kind'] for i in items))},runtime=dict(self.runtime))
        return result

    def _experiments_view(self,identity,params):
        from rule_experiments import active_rules
        if identity:return {'item':self.experiments.get(identity)}
        values=self.experiments.list(limit=100)
        with self.db() as db:
            candidates=self.experiments.candidates()
            active=active_rules(db)
        return dict(paginate(values['items'],params),candidates=candidates,active_rules=active,
                    archive_total=values['total'])

    def mutate(self,resource,payload):
        if not isinstance(payload,dict):raise ValueError('객체 형식의 요청이 필요합니다.')
        action=payload.get('action','')
        if resource=='query':return {'run':self.jobs.start(self._analysis_snapshot(payload))}
        if resource=='experiments':return self._experiment_action(action,payload)
        if resource=='scenarios' and action=='compare':
            return {'run':self.jobs.start(self._analysis_snapshot(payload,True))}
        if resource=='scenarios' and action=='draft':return self._simulation_draft(payload)
        with self.lock,self.db() as db:
            lookup=lambda ref:get_document(db,ref)
            if resource in ('events','concepts'):
                routes={'events':{'merge':'event_merge','split':'event_split','source':'source_attribution'},
                        'concepts':{'alias':'concept_alias','split':'concept_split','exclude':'concept_exclude'}}
                if action not in routes[resource]:raise ValueError('지원하지 않는 변경 작업입니다.')
                item=mutate_store(db,routes[resource][action],payload)
                self.last_token=None;self.wake.set()
                return {'item':item,'refresh_pending':True}
            if resource=='profiles' and action=='save':
                item=save_record(db,'profile',profile_value(payload),'평가 가중치 저장',payload.get('id') or None)
            elif resource=='decisions' and action in ('create','update','review'):
                old=get_record(db,payload.get('id'),'decision') if action!='create' else None
                if action!='create' and not old:raise ValueError('결정 기록을 찾을 수 없습니다.')
                if action=='review':
                    topic=query_store(db,'topic',{'id':old.get('topic_id')}) or {}
                    item=refresh_record(db,old,lookup,topic.get('document_ids',[]))
                else:
                    value=decision_value(payload,lookup,old)
                    if value['topic_id'] and not query_store(db,'topic',{'id':value['topic_id']}):raise ValueError('연결할 주제를 찾을 수 없습니다.')
                    item=save_record(db,'decision',value,'전략 선택과 가설 기록',old['id'] if old else None)
            elif resource=='scenarios' and action in ('create','update'):
                old=get_record(db,payload.get('id'),'scenario') if action=='update' else None
                if action=='update' and not old:raise ValueError('시나리오를 찾을 수 없습니다.')
                value=scenario_value(payload,lookup,old)
                if value['topic_id'] and not query_store(db,'topic',{'id':value['topic_id']}):raise ValueError('연결할 주제를 찾을 수 없습니다.')
                if value['decision_id'] and not get_record(db,value['decision_id'],'decision'):raise ValueError('연결할 결정을 찾을 수 없습니다.')
                item=save_record(db,'scenario',value,'시나리오 가정 기록',old['id'] if old else None)
            elif resource=='research' and action=='milestone':item=milestone(db,payload,lookup)
            else:raise ValueError('지원하지 않는 변경 작업입니다.')
        self.wake.set()
        return {'item':item}

    def _analysis_snapshot(self,payload,scenario=False):
        with self.db() as db:
            documents={d['id']:d for d in entities(db,'document') if d['status']=='current'}
            claims=[c for c in entities(db,'claim') if c['status']=='current_reviewed']
            reviewed={ref for c in claims for ref in c['document_ids']}
            excluded={r[0] for r in db.execute('SELECT id FROM intel_exclusions WHERE excluded=1')}
            topics=[t for t in entities(db,'topic') if t.get('status')!='dormant' and t['id'] not in excluded and t.get('concept_id') not in excluded]
            if scenario:
                ids=strings(payload.get('ids',[]),'비교 시나리오',maximum=4)
                if len(ids)<2:raise ValueError('서로 다른 시나리오 2~4개를 선택해 주세요.')
                subjects=[get_record(db,i,'scenario') for i in ids]
                if any(s is None for s in subjects):raise ValueError('비교 시나리오를 찾을 수 없습니다.')
                selected=[]
                for s in subjects:
                    refs=s.get('evidence_ids',[])+next((t['document_ids'] for t in topics if t['id']==s.get('topic_id')),[])
                    own=[ref for ref in refs if ref in reviewed and ref in documents]
                    if not own:raise ValueError('각 시나리오에 현재 검토된 근거 문서를 연결해 주세요.')
                    selected.extend(own[:max(1,16//len(subjects))])
                question='시나리오의 서로 다른 가정이 영향을 바꾸는 경로와 반증 조건을 비교하세요.'
                mode='conditional'
            else:
                question=text(payload.get('question',''),'질문',True,2000)
                if len(question)<3:raise ValueError('질문은 3자 이상이어야 합니다.')
                mode=payload.get('mode','hybrid')
                if mode not in ('local','global','hybrid'):raise ValueError('검색 범위를 확인해 주세요.')
                ids=strings(payload.get('topic_ids',[]),'전략 주제',maximum=16)
                if ids and set(ids)-{t['id'] for t in topics}:raise ValueError('선택한 주제가 없습니다.')
                if ids:topics=[t for t in topics if t['id'] in ids]
                from evidence_search import LexicalIndex, query_anchors, terms
                claim_scores=LexicalIndex({c['id']:[(c['title'],3),(c['detail'],1)] for c in claims}).search(question)
                anchors=query_anchors(question)
                if anchors:
                    claim_scores={c['id']:claim_scores[c['id']] for c in claims if c['id'] in claim_scores
                                  and set(terms(c['title']+' '+c['detail']))&anchors}
                topic_scores=LexicalIndex({t['id']:[(t['label'],3)] for t in topics}).search(question)
                score=lambda t:topic_scores.get(t['id'],0)+max((claim_scores.get(c,0) for c in t['claim_ids']),default=0)
                topics.sort(key=lambda t:(-score(t),t['id']))
                chosen=[t for t in topics if ids or score(t)>0][:1 if mode=='local' else 8]
                subjects=[];selected=[];groups=[]
                for t in chosen:
                    relevant=[c for c in claims if c['id'] in t['claim_ids'] and (ids or c['id'] in claim_scores)]
                    relevant.sort(key=lambda c:(-claim_scores.get(c['id'],0),c['id']))
                    if not relevant:continue
                    subjects.append({'id':t['id'],'title':t['label'],'claim_count':len(relevant),
                        'reviewed_claims':[{k:c[k] for k in ('title','detail','uncertainty','document_ids')} for c in relevant[:4]]})
                    groups.append(list(dict.fromkeys(ref for c in relevant[:4] for ref in c['document_ids'] if ref in documents)))
                # Give every selected community evidence before filling the remaining budget.
                for offset in range(max(map(len,groups),default=0)):
                    selected.extend(group[offset] for group in groups if offset<len(group))
                if not subjects:
                    if ids or mode=='local':raise ValueError('선택한 주제에는 현재 검토된 근거가 없습니다.')
                    chosen_claims=sorted((c for c in claims if c['id'] in claim_scores),
                                         key=lambda c:(-claim_scores[c['id']],c['id']))[:8]
                    if not chosen_claims:raise ValueError('질문과 관련된 현재 검토 근거를 찾지 못했습니다.')
                    subjects=[{'id':'corpus','title':'현재 검토된 자료','reviewed_claims':chosen_claims}]
                    selected=[ref for c in chosen_claims for ref in c['document_ids'] if ref in documents]
            selected=list(dict.fromkeys(selected))[:16]
            if not scenario:
                allowed=set(selected)
                for subject in subjects:
                    subject['reviewed_claims']=[c for c in subject.get('reviewed_claims',[]) if set(c['document_ids'])<=allowed]
            evidence=[{k:documents[ref].get(k) for k in ('id','title','url','source_url','version','source_hash','status','published_day','date_basis')} |
                {'text':(documents[ref].get('source_text') or documents[ref].get('snapshot_text') or documents[ref].get('text',''))[:3500]} for ref in selected]
            return {'kind':'scenario_compare' if scenario else 'question','question':question,'mode':mode,
                'subjects':subjects,'evidence':evidence,'coverage':{'available_reviewed_documents':len(reviewed),
                'selected_documents':len(evidence),'available_topics':len(topics),'selected_subjects':len(subjects),
                'scope':'검색된 현재 검토 근거의 제한된 종합; 전체 자료를 모두 읽었다는 뜻이 아님'}}

    def _simulation_draft(self,payload):
        if not self.simulation:raise ValueError('MiroFish 연결 서비스가 준비되지 않았습니다.')
        with self.db() as db:
            item=get_record(db,payload.get('id'),'scenario')
            if not item:raise ValueError('시나리오를 찾을 수 없습니다.')
            topic=query_store(db,'topic',{'id':item.get('topic_id')}) or {}
            refs=list(dict.fromkeys(item.get('evidence_ids',[])+topic.get('document_ids',[])))[:24]
            claims=[c for c in entities(db,'claim') if c['status']=='current_reviewed']
            reviewed={d for c in claims for d in c['document_ids']}
            documents=[get_document(db,ref) for ref in refs if ref in reviewed]
            documents=[d for d in documents if d and d['status']=='current']
        if not documents:raise ValueError('MiroFish 초안에 연결할 현재 검토된 자료가 없습니다.')
        requirement=('조건부 가정의 비교 실험. 실제 정책 시행이나 발생 확률을 확정하지 말고 가정·관측·반증조건을 구분한다. '
                     +json.dumps({'assumptions':item['assumptions'],'conditions':item['conditions']},ensure_ascii=False))
        if len(requirement)>4000:raise ValueError('시뮬레이션 가정 설명을 4,000자 이내로 줄여 주세요.')
        run=self.simulation.create_run([dict(d,text=d.get('source_text') or d['text'],day=d.get('published_day')) for d in documents],
            {'title':item['title'][:200],'requirement':requirement,'rounds':20})
        with self.db() as db:
            item=save_record(db,'scenario',dict(item,mirofish_run_ids=list(dict.fromkeys(item.get('mirofish_run_ids',[])+[run['id']]))),
                             '조건부 MiroFish 실행 초안 연결',item['id'])
        return {'item':item,'run':run}

    def _experiment_action(self,action,payload):
        if action=='candidate':return {'item':self.experiments.create_candidate(payload)}
        if action=='promote':return {'item':self.experiments.promote(payload.get('id'))}
        if action=='rollback':return {'item':self.experiments.rollback(payload.get('activation_id') or None)}
        if action=='resume':return {'run':dict(self.experiments.resume(payload.get('id')),kind='rule_experiment')}
        if action!='start':raise ValueError('지원하지 않는 실험 작업입니다.')
        count=max(2,min(12,int(payload.get('case_count',6))))
        with self.db() as db:
            claims=[c for c in entities(db,'claim') if c['status']=='current_reviewed']
            reviewed=list(dict.fromkeys(ref for c in claims for ref in c['document_ids']))
            docs=[get_document(db,ref) for ref in reviewed]
            from rule_experiments import digest as rule_digest
            used=set()
            for row in db.execute('SELECT payload_json FROM rule_experiments'):
                used.update(json.loads(row[0]).get('holdout_keys',[]))
            docs=[d for d in docs if d and d['status']=='current' and
                  rule_digest([d['url'],d['source_text'] or d['snapshot_text']]) not in used][:count]
            if len(docs)<2:raise ValueError('실험에는 현재 검토된 독립 문서 두 개 이상이 필요합니다.')
            cases=[{'id':d['id'],'split':'holdout' if i>=len(docs)//2 else 'development',
                'evidence':[{'id':d['id'],'url':d['url'],'title':d['title'],'text':d['source_text'] or d['snapshot_text'],
                             'origin':'fetched_url_excerpt' if d['source_text'] else 'telegram_excerpt',
                             'version':d['version'],'source_hash':d['source_hash']}]} for i,d in enumerate(docs)]
        result=self.experiments.start({'candidate_id':payload.get('candidate_id'),'cases':cases,
            'settings':{'language':'ko','focus':'현재 검토된 고정 원문: 인용·위험·국가 귀속'}})
        return {'run':dict(result,kind='rule_experiment')}

    def close(self):
        self.stop.set();self.wake.set()
        if self.worker:self.worker.join(timeout=2)
        self.jobs.close();self.experiments.close()
