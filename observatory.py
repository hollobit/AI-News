"""Bounded, source-backed temporal topic/keyword graph; no model or network calls."""
import re
import time
from collections import defaultdict
from datetime import date,timedelta,datetime,timezone
from itertools import combinations
from keyword_index import document_id,keyword_record_id
from projection_cache import cached_read,content_digest


MAX_TOPICS=36
MAX_KEYWORDS=48
MAX_EDGES=180
EXPANDED_TOPICS=72
EXPANDED_KEYWORDS=96
EXPANDED_EDGES=360


def meaningful_label(label):
    # Navigation, citation menus and maintenance banners are not strategic phrases.
    return bool(label and not re.search(r'[\r\n]|\b(?:book now|under construction|NASA ADS|AM PDT|PM PDT)\b',label,re.I))


def select_topics(trends, limit=MAX_TOPICS):
    """Balance established, rising and declining themes without relaxing evidence gates."""
    groups=sorted([t for t in trends['monitoring']['topics'] if t['current']>0 and meaningful_label(t.get('label') or t.get('name') or t['id'])],key=lambda t:(-t['current'],t['id']))[:8]
    eligible=[t for t in trends['lenses'] if t['current']>0 and meaningful_label(t.get('label') or t.get('name') or t['id'])]
    builtin=sorted([t for t in eligible if not t.get('dynamic')],key=lambda t:-t['current'])
    auto=[t for t in eligible if t.get('dynamic')]
    rankings=[builtin,sorted(auto,key=lambda t:-(t['current']-t['previous'])/max(1,t['previous'])),
              sorted([t for t in auto if t['current']<t['previous']],key=lambda t:(t['current']-t['previous'])/max(1,t['previous'])),
              sorted(auto,key=lambda t:-t['current'])]
    chosen=list(groups)+[t for t in eligible if t['id'] in ('medical','public_ax') and t['id'] not in {g['id'] for g in groups}];seen={t['id'] for t in chosen}
    for index in range(max((len(r) for r in rankings),default=0)):
        for ranking in rankings:
            if index>=len(ranking):continue
            topic=ranking[index]
            if topic['id'] not in seen:chosen.append(topic);seen.add(topic['id'])
            if len(chosen)>=limit:return chosen
    return chosen


def build_projection(items,morph,topics,memberships,days,previous_items=None,
                     topic_limit=MAX_TOPICS,keyword_limit=MAX_KEYWORDS,edge_limit=MAX_EDGES):
    """Edges join exact (canonical document, date) observations, never cross dates."""
    valid=set(days); observations={}; keyword_docs=defaultdict(set); labels={}
    for item in items:
        if item.get('day') not in valid:continue
        key=(document_id(item),item['day'])
        observations.setdefault(key,item)
        for term in morph.get(keyword_record_id(item),[]):
            if term.get('kind')=='noun' or not meaningful_label(term['label']):continue
            identity='keyword:'+term['id'];labels[identity]=term['label'];keyword_docs[identity].add(key)
    # Show repeated, explicit phrases; scope limits are exposed in the response.
    ranked=sorted(keyword_docs,key=lambda k:(-len({d for d,_ in keyword_docs[k]}),labels[k]))
    eligible=[k for k in ranked if len({d for d,_ in keyword_docs[k]})>=2]
    selected=[]
    for topic in topics:
        docs=set(memberships.get(topic['id'],()))&observations.keys()
        related=sorted(eligible,key=lambda k:(-len(keyword_docs[k]&docs)/max(1,len(keyword_docs[k])),-len(keyword_docs[k]&docs),k))
        candidate=next((k for k in related if k not in selected and len({d for d,_ in keyword_docs[k]&docs})>=2),None)
        if candidate:selected.append(candidate)
        if len(selected)>=keyword_limit:break
    selected=(selected+[k for k in eligible if k not in selected])[:keyword_limit]
    supports={k:keyword_docs[k] for k in selected};meta={k:dict(id=k,label=labels[k],kind='keyword',keyword_id=k[8:]) for k in selected}
    for topic in topics:
        identity=topic['id'];supports[identity]=set(memberships.get(identity,()))&observations.keys()
        meta[identity]=dict(id=identity,label=topic.get('label') or topic['name'],kind='topic',lens=identity,
                            origin='grouped' if topic.get('grouped') else topic.get('origin','builtin'))
    prior_supports=defaultdict(set)
    if previous_items is not None:
        prior_keys={(document_id(i),i['day']) for i in previous_items}
        for item in previous_items:
            for term in morph.get(keyword_record_id(item),[]):
                if term.get('kind')!='noun':prior_supports['keyword:'+term['id']].add((document_id(item),item['day']))
        for topic in topics:prior_supports[topic['id']]=set(memberships.get(topic['id'],()))&prior_keys
    evidence={};documents={}
    def summary(keys,prior=(),complete=False):
        ordered=sorted(keys,key=lambda k:(k[1],k[0]),reverse=True)
        by_day=defaultdict(list)
        for key in ordered:by_day[key[1]].append(key)
        daily=[];samples=[];all_ids=[]
        for day in days:
            matches=by_day[day];daily.append(len({k[0] for k in matches}));ids=[]
            for key in matches[:3]:
                eid=content_digest(key)[:20];item=observations[key]
                evidence[eid]=dict(id=eid,document_id=key[0],day=key[1],title=str(item.get('title') or '수집 뉴스')[:200],
                    url=item.get('source_url') or '',item_id=item.get('item_id') or keyword_record_id(item),
                    excerpt=str(item.get('excerpt') or item.get('text') or '')[:600])
                ids.append(eid)
            samples.append(ids)
            if complete:
                full=[]
                for key in matches:
                    eid=content_digest(key)[:20];item=observations[key]
                    documents[eid]=dict(id=eid,document_id=key[0],day=key[1],title=str(item.get('title') or '수집 뉴스')[:200],url=item.get('source_url') or '')
                    full.append(eid)
                all_ids.append(full)
        return dict(count=len({d for d,_ in keys}),series=daily,evidence_by_day=samples,
                    **({'document_ids_by_day':all_ids} if complete else {}),
                    current=len({d for d,_ in keys}) if previous_items is not None else len({d for d,day in keys if day>=days[-7]}),
                    previous=len({d for d,_ in prior}) if previous_items is not None else len({d for d,day in keys if days[-14]<=day<days[-7]}))
    nodes=[dict(meta[k],**summary(supports[k],prior_supports[k],complete=True)) for k in supports if supports[k]]
    candidates=[]
    for a,b in combinations([n['id'] for n in nodes],2):
        if meta[a]['kind']==meta[b]['kind']=='topic':continue
        shared=supports[a]&supports[b]
        if len({d for d,_ in shared})>=2:candidates.append((a,b,shared))
    # Reserve a few links for each topic so frequent topics cannot hide rare signals.
    candidates.sort(key=lambda e:(meta[e[0]]['kind']==meta[e[1]]['kind'], -len({d for d,_ in e[2]}),e[0],e[1]))
    chosen=[];seen=set()
    def include(entry):
        pair=entry[:2]
        if pair not in seen and len(chosen)<edge_limit:chosen.append(entry);seen.add(pair)
    for topic in topics:
        for entry in [e for e in candidates if topic['id'] in e[:2]][:3]:include(entry)
    for entry in [e for e in candidates if meta[e[0]]['kind']==meta[e[1]]['kind']][:36]:include(entry)
    for entry in candidates:include(entry)
    edges=[dict(id='edge:'+content_digest([a,b])[:16],source=a,target=b,relation='같은 문서·날짜에 관측',**summary(keys,prior_supports[a]&prior_supports[b])) for a,b,keys in chosen]
    # Exact article memberships; evidence_by_day remains a bounded display sample.
    from link_groups import canonical_url
    article_nodes={}
    for identity,keys in supports.items():
        for key in keys:
            url=canonical_url(observations[key].get('source_url') or '')
            if url:article_nodes.setdefault(url,set()).add(identity)
    article_nodes={url:sorted(ids) for url,ids in article_nodes.items()}
    result=dict(selection_version=2,document_index_version=1,documents=documents,comparison_days=len(days) if previous_items is not None else 7,days=days,nodes=nodes,edges=edges,evidence=evidence,article_nodes=article_nodes,
        limits={'keywords':keyword_limit,'topics':len(topics),'edges':edge_limit,
                'topic_limit':topic_limit,'keyword_limit':keyword_limit,'edge_limit':edge_limit,
                'evidence_per_day':3,'eligible_keywords':len(eligible),'eligible_edges':len(candidates)},
        method=f'선택한 {len(days)}일의 실제 관측으로 주제·키워드를 선정하고 연결을 재집계합니다. 선은 같은 문서·같은 날짜의 공동 관측이며 인과관계가 아닙니다. 전체 문서 목록은 기간별·일별 정규 문서 ID로 중복 제거합니다. 원문 발췌는 날짜별 최대 3개 표본이며 전체 문서 제목·출처 목록과 구분합니다.')
    result['version']=content_digest(result)
    return result


def candidate_context(db, data):
    """Discover rules without computing the unrelated seven-day overview."""
    from strategy_trends import LENSES
    from dynamic_strategy import dynamic_projection
    from dynamic_registry import list_registry
    discovered = dynamic_projection(db, data['items'], data['morph'])
    excluded = {r['id'] for r in list_registry(db)['items'] if r.get('excluded')}
    valid_days = []
    for item in data['items']:
        try:valid_days.append(date.fromisoformat(item['day']))
        except (ValueError, KeyError):pass
    return {'end': max(valid_days, default=date.today()).isoformat(),
            'lenses': [t for t in list(LENSES) + discovered.get('topics', []) if t['id'] not in excluded],
            'monitoring': {'topics': []}}


def read_observatory(db, window=14, expanded=False):
    if window not in (14,30,90):raise ValueError("관측 기간은 14, 30, 90일 중 선택해 주세요.")
    from strategy_views import dataset
    from strategy import select_strategy_items
    from projection_cache import revision_token
    from dynamic_registry import list_registry
    revision=revision_token(db,('source',))
    registry_version=content_digest([{k:entry.get(k) for k in ('id','kind','label','terms','origin','excluded')} | {'keyword_id':(entry.get('metadata') or {}).get('keyword_id')} for entry in list_registry(db).get('items',[])])
    def build():
        started=time.perf_counter();timing={}
        data=dataset(db)
        timing['dataset_seconds']=round(time.perf_counter()-started,3);stage=time.perf_counter()
        trends=candidate_context(db,data)
        timing['overview_seconds']=round(time.perf_counter()-stage,3);stage=time.perf_counter()
        end=date.fromisoformat(trends['end']);days=[(end-timedelta(days=window-1-i)).isoformat() for i in range(window)]
        items=[i for i in data['items'] if days[0]<=i.get('day','')<=days[-1]]
        previous_start=(end-timedelta(days=2*window-1)).isoformat()
        previous_end=(end-timedelta(days=window)).isoformat()
        previous_items=[i for i in data['items'] if previous_start<=i.get('day','')<=previous_end]
        # Include dormant signals as candidates: a recent-week view may have omitted them.
        from monitoring_groups import family_for
        from strategy_monitoring import WATCH_LENSES
        registry=list_registry(db).get('items',[])
        excluded={t['id'] for t in registry if t.get('excluded')}
        groups={t['id']:t for t in trends['monitoring']['topics']}
        for entry in list(WATCH_LENSES)+[t for t in registry if t.get('kind')=='signal' and t.get('origin')!='builtin']:
            if entry['id'] in excluded:continue
            identity,label=family_for(entry)
            groups.setdefault(identity,dict(id=identity,label=label,grouped=True))
        # All windows share the same evidence-checked 180-day memberships.
        # Morphology postings only narrow automatic candidates; the existing
        # matcher still checks source spans and strategic/control context.
        candidate_topics=trends['lenses']+list(groups.values())
        def match_memberships():
            earliest=(end-timedelta(days=179)).isoformat()
            corpus=[i for i in data['items'] if earliest<=i.get('day','')<=days[-1]]
            entries={t['id']:t for t in registry};postings=defaultdict(list)
            for item in corpus:
                for kid in {t['id'] for t in data['morph'].get(keyword_record_id(item),[])}:
                    postings[kid].append(item)
            output={}
            for topic in candidate_topics:
                entry=entries.get(topic['id'],{});kid=(entry.get('metadata') or {}).get('keyword_id') or entry.get('keyword_id')
                automatic=topic['id'].startswith(('dynamic:','dynamic-signal:')) and entry.get('origin')!='manual'
                candidates=postings.get(kid,[]) if automatic and kid else corpus
                found=select_strategy_items(db,{'lens':[topic['id']]},candidates,data['morph'],precomputed=True,registry=registry,membership_only=True)
                output[topic['id']]={(document_id(i),i['day']) for i in found}
            return output
        membership_key=(revision,registry_version,end.isoformat(),tuple(t['id'] for t in candidate_topics)) if revision else None
        memberships=cached_read(db,'observatory-memberships-v1',membership_key,match_memberships,copy_result=False)
        timing['memberships_seconds']=round(time.perf_counter()-stage,3);stage=time.perf_counter()
        ranked={'lenses':[],'monitoring':{'topics':[]}}
        for kind,candidates in [('lenses',trends['lenses']),('groups',list(groups.values()))]:
            for topic in candidates:
                keys=memberships[topic['id']]
                current=len({d for d,day in keys if days[0]<=day<=days[-1]})
                previous=len({d for d,day in keys if previous_start<=day<=previous_end})
                row=dict(topic,current=current,previous=previous)
                (ranked['lenses'] if kind=='lenses' else ranked['monitoring']['topics']).append(row)
        topic_limit=EXPANDED_TOPICS if expanded else MAX_TOPICS
        keyword_limit=EXPANDED_KEYWORDS if expanded else MAX_KEYWORDS
        edge_limit=EXPANDED_EDGES if expanded else MAX_EDGES
        topics=select_topics(ranked,topic_limit)
        result=build_projection(items,data['morph'],topics,memberships,days,previous_items,
                                topic_limit,keyword_limit,edge_limit)
        available=sorted({i['day'] for i in data['items'] if i.get('day')})
        result['comparison']={'current_start':days[0],'current_end':days[-1],
            'previous_start':previous_start,'previous_end':previous_end,
            'earliest_available':available[0] if available else None,
            'previous_observed_days':len({i['day'] for i in previous_items}),
            'current_observed_days':len({i['day'] for i in items}),
            'limited_previous_coverage':len({i['day'] for i in previous_items})<window}
        result['method']+=f' 증감은 선택한 {window}일과 직전 {window}일의 고유 문서 수 비교입니다. 보관 자료가 없는 날짜의 0건은 실제 사건이 없었다는 뜻이 아닙니다.'
        result['limits']['expanded']=expanded
        result['limits']['default_topics']=MAX_TOPICS
        result['limits']['default_keywords']=MAX_KEYWORDS
        result['limits']['default_edges']=MAX_EDGES
        result['version']=content_digest(result)
        timing['projection_seconds']=round(time.perf_counter()-stage,3)
        result['timing']=dict(timing,total_seconds=round(time.perf_counter()-started,3))
        result['computed_at']=datetime.now(timezone.utc).isoformat()
        return result
    key=(revision,registry_version,window,expanded) if revision else None
    return cached_read(db,f'observatory-v11-{window}-{"expanded" if expanded else "default"}',key,build)
