"""Small independently loaded views over revisioned, immutable news projections."""
from task_lifecycle import checkpoint
from collections import Counter
from datetime import datetime
import json
import re
from copy import deepcopy
from projection_cache import cached_read, revision_token
from keyword_index import keyword_record_id


def _share_projection_strings(value):
    """Share equal immutable strings within one projection without changing values.

    Keyword identifiers and morphology labels recur across thousands of articles.
    Keep this pool local to the snapshot so old source revisions can be released.
    Copy containers while preserving shared references within the new snapshot.
    """
    strings, containers = {}, {}
    def share(item):
        if isinstance(item, str):
            return strings.setdefault(item, item)
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in containers:
                return containers[identity]
            result = {} if isinstance(item, dict) else []
            containers[identity] = result
            if isinstance(item, dict):
                result.update((share(key), share(val)) for key, val in item.items())
            else:
                result.extend(share(val) for val in item)
            return result
        return item
    return share(value)


def dataset(db):
    revision = revision_token(db, ('source',))
    def build():
        from news_repository import read_news, joined_articles
        from keyword_index import ensure_keyword_index, _record_mappings
        ensure_keyword_index(db, lambda: joined_articles(db))
        # This projection supplies its own morphology/keyword views; avoid
        # constructing the separate legacy discovery panel for the entire corpus.
        news = read_news(db, {'date':['all']}, include_discovery=False)
        raw = news.pop('items')
        mappings = _record_mappings(db, [keyword_record_id(i) for i in raw])
        from strategy_projection import prepare_documents
        projected, morph = prepare_documents(db, raw, mappings)
        return _share_projection_strings({'news':news,'items':projected,'morph':morph,'revision':revision})
    return cached_read(db,'strategy-dataset-v1',revision,build,copy_result=False)


def selected(db, params, data):
    from strategy import select_strategy_items
    items = data['items']
    get = lambda k,default='': params.get(k,[default])[0]
    day = get('date','all')
    if day != 'all':
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day): raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
        datetime.strptime(day,'%Y-%m-%d')
    keyword = get('keyword')
    if keyword and not re.fullmatch(r'[0-9a-f]{16}',keyword): raise ValueError('키워드 ID 형식이 올바르지 않습니다.')
    query = get('q').strip()
    items = [i for i in items if (day=='all' or i['day']==day)
        and (not get('channel') or str(i['chat_id'])==get('channel'))
        and (not keyword or keyword in i['_all_keyword_ids'])
        and (not get('topic') or i['topic']==get('topic'))
        and (not get('content_type') or i['content_type']==get('content_type'))]
    if query:
        from news_search import filter_items
        items = filter_items(db, items, query)
    return select_strategy_items(db,dict({'sort':['strategic']},**params),items,records=data['morph'],precomputed=True)


def enrich(db, items):
    from bulk_baseline import read_baseline
    from recursive_improvement import news_identity
    from link_groups import canonical_url, _stable_hash
    baselines = read_baseline(db,items)
    has_research = db.execute("SELECT 1 FROM sqlite_master WHERE name='research_documents'").fetchone()
    result=[]
    for item in items:
        checkpoint()
        url=canonical_url(item.get('source_url') or '')
        analysis=None
        if url and has_research:
            row=db.execute("""SELECT d.analysis_json,d.run_id,d.doc_id,d.source_status FROM research_documents d
                JOIN research_runs r ON r.id=d.run_id WHERE d.canonical_url=? AND d.status='complete'
                ORDER BY r.created_at DESC LIMIT 1""",(url,)).fetchone()
            if row: analysis=dict(json.loads(row['analysis_json']),run_id=row['run_id'],doc_id=row['doc_id'],source_status=row['source_status'],freshness='historical_unchecked')
        result.append(dict(deepcopy({k:v for k,v in item.items() if not k.startswith('_')}),
            group_id=_stable_hash(url,length=24) if url else None,
            strategic_analysis=analysis,base_analysis=baselines.get(news_identity(item))))
    return result


def card(item):
    keys=('item_id','keyword_record_id','chat_id','message_id','item_index','title','excerpt','day','topic','content_type','type_label','type_reason','source_url','original_url','url','channel','published_at','sectors','strategic_value','group_id','source_count','matched_terms','search_matches')
    result={k:item[k] for k in keys if k in item}
    for key in ('title','excerpt'): result[key]=str(result.get(key) or '')[:300]
    result['strategic_keywords']=[{k:t[k] for k in ('id','label','kind') if k in t} for t in item.get('strategic_keywords',[])[:6]]
    for key in ('base_analysis','strategic_analysis'):
        checkpoint()
        value=item.get(key)
        if value:
            compact={k:value[k] for k in ('status','verified','summary','run_id','doc_id','freshness') if k in value}
            nested=value.get('result') or {}
            compact['summary']=value.get('summary') or (nested.get('summary') if isinstance(nested,dict) else '')
            result[key]=compact
        else:
            result[key]=None
    result['source_context']={k:v for k,v in (item.get('source_context') or {}).items() if k in ('status','title','fetched_at','error')}
    return deepcopy(result)


def read_item(db, item_id):
    item=next((i for i in dataset(db)['items'] if i['item_id']==item_id),None)
    return {'item':enrich(db,[item])[0]} if item else None


def read_view(db, params, sources):
    data=dataset(db)
    if params.get('view',['news'])[0]=='overview':
        from strategy_trends import trend_metrics
        from app import link_groups_for
        # The aggregate belongs to this dataset snapshot, not a newer revision
        # that may have arrived between the two reads.
        revision=data.get('revision')
        def aggregate():
            return {'trends':trend_metrics(db,data['items'],records=data['morph'],precomputed=True),
                    'url_groups':len(link_groups_for(db))}
        overview=cached_read(db,'strategy-overview-v2-domains',revision,aggregate)
        from dynamic_strategy import apply_dynamic_trends
        overview['trends']=apply_dynamic_trends(db,data['items'],data['morph'],overview['trends'],revision)
        runs=[]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='research_runs'").fetchone():
            for row in db.execute('SELECT id,status,processed_documents,total_documents,error,report_json FROM research_runs ORDER BY created_at DESC LIMIT 12'):
                checkpoint()
                run=dict(row); report=json.loads(run.pop('report_json') or 'null')
                run['report']={k:report[k] for k in ('summary','outlooks','major_topics') if k in report} if report else None
                runs.append(run)
        heartbeat=db.execute("SELECT value FROM state WHERE key='collector_last_success'").fetchone()
        return dict(overview,version=overview['trends']['version'],total=len(data['items']),sources=sources,runs=runs,
                    collector={'last_success':heartbeat[0] if heartbeat else None,'last_message':db.execute('SELECT MAX(published_at) FROM news').fetchone()[0]})
    items=selected(db,params,data)
    page=max(1,int(params.get('page',['1'])[0])); size=max(1,min(24,int(params.get('page_size',['12'])[0])))
    from sector_taxonomy import sector_counts
    from strategic_value import DOMAINS
    counts=Counter(d['id'] for i in items for d in i['strategic_value']['domains'])
    result={k:deepcopy(data['news'][k]) for k in ('dates','topics','types')}
    from keyword_index import document_id
    unique_documents=len({document_id(item) for item in items})
    result.update(items=[card(i) for i in enrich(db,items[(page-1)*size:page*size])],total=len(items),
        unique_documents=unique_documents,page=page,page_size=size,pagination={'page':page,'page_size':size,'has_more':page*size<len(items)},
        sectors=sector_counts(items),impact_domains=[dict(id=d['id'],label=d['label'],count=counts[d['id']]) for d in DOMAINS],
        scope={k:v[0] for k,v in params.items()})
    return result
