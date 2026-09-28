"""Versioned, source-bound strategic projections. No inferred fact confirmation."""
import hashlib
import json
import re
import unicodedata
from collections import defaultdict, Counter
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from link_groups import canonical_url

VERSION = 'strategic-store-v2-reviewed-events'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _id(kind, value):
    return kind+':'+_hash(value)[:24]


def _normal(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value))).strip().casefold()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_store(db):
    db.execute('CREATE TABLE IF NOT EXISTS intel_entities(id TEXT PRIMARY KEY,kind TEXT NOT NULL,version INTEGER NOT NULL,content_hash TEXT NOT NULL,payload_json TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS intel_entities_kind ON intel_entities(kind)')
    for plural,kind in [('documents','document'),('claims','claim'),('events','event'),('topics','topic'),('concepts','concept')]:
        db.execute("CREATE VIEW IF NOT EXISTS intel_"+plural+" AS SELECT id,version,content_hash,payload_json FROM intel_entities WHERE kind='"+kind+"'")
    db.execute('CREATE TABLE IF NOT EXISTS intel_review_cache(run_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,payload_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intel_source_attributions(document_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intel_exclusions(id TEXT PRIMARY KEY,excluded INTEGER NOT NULL,reason TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intel_history(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS intel_history_id ON intel_history(id,version)')
    db.execute('CREATE TABLE IF NOT EXISTS intel_dependencies(parent_id TEXT NOT NULL,child_id TEXT NOT NULL,PRIMARY KEY(parent_id,child_id))')
    db.execute('CREATE TABLE IF NOT EXISTS intel_event_assignments(document_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,reason TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intel_aliases(alias_key TEXT PRIMARY KEY,concept_id TEXT NOT NULL,language TEXT NOT NULL,surface TEXT NOT NULL,reason TEXT NOT NULL)')


def _rows(db, kind=None):
    sql='SELECT payload_json FROM intel_entities'+(' WHERE kind=?' if kind else '')
    return {x['id']:x for row in db.execute(sql,(kind,) if kind else ()) for x in [json.loads(row[0])]}


def _put(db, kind, value, reason, parents=()):
    value=dict(value,kind=kind)
    value.pop('version',None)
    digest=_hash(value)
    old=db.execute('SELECT version,content_hash FROM intel_entities WHERE id=?',(value['id'],)).fetchone()
    if old and old[1]==digest:
        return False
    value['version']=old[0]+1 if old else 1
    raw=_json(value)
    db.execute('INSERT OR REPLACE INTO intel_entities VALUES (?,?,?,?,?)',(value['id'],kind,value['version'],digest,raw))
    db.execute('INSERT INTO intel_history(id,version,reason,created_at,payload_json) VALUES (?,?,?,?,?)',(value['id'],value['version'],reason,_now(),raw))
    db.execute('DELETE FROM intel_dependencies WHERE child_id=?',(value['id'],))
    db.executemany('INSERT OR IGNORE INTO intel_dependencies VALUES (?,?)',[(p,value['id']) for p in parents])
    return True


def _source_document(item):
    from improvement_selection import content_identity
    # item.url is often a shared Telegram message, not this article's source.
    url=canonical_url(item.get('source_url') or '')
    identity=content_identity(item)
    source=item.get('source_context') or {}
    source_text=str(source.get('text') or '') if source.get('status')=='fetched' else ''
    origin=canonical_url(item.get('original_source_url') or '')
    day=str(item.get('day') or '')[:10] if item.get('date_basis')=='article' else ''
    try: date.fromisoformat(day)
    except ValueError:day=''
    return {'id':_id('document',identity),'corpus_identity':identity,
        'archive_url':str(item.get('url') or ''),'url':url,'source_url':url,'title':str(item.get('title') or ''),
        'text':str(item.get('text') or ''),'snapshot_text':'\n'.join(str(item.get(k) or '') for k in ('title','summary','text','description')).strip()[:2000],
        'source_text':source_text[:3500],'source_title':str(source.get('title') or '') if source_text else '',
        'source_status':source.get('status','unavailable'),'source_hash':_hash([source_text,source.get('status'),source.get('title')]),
        'published_day':day,'date_basis':'article' if day else 'unknown','collected_at':str(item.get('collected_at') or item.get('first_seen_at') or ''),
        'collection_date_basis':'explicit_collection_timestamp' if item.get('collected_at') or item.get('first_seen_at') else 'unknown',
        'telegram_published_at':str(item.get('published_at') or ''),
        'channel':str(item.get('channel') or ''),'countries_mentioned':list(item.get('country_terms') or []),
        'topics':list(item.get('topics') or []),'language':str(item.get('language') or 'unknown'),
        'publisher':urlsplit(url).hostname or 'unknown','original_source_url':origin,
        'origin_status':'explicit_source_reference' if origin else 'unknown',
        'independent_confirmation':'unknown','mention_count':max(1,int(item.get('mention_count') or 1)),
        'status':'current'}


def _matches(evidence, doc):
    if not doc or doc.get('status')!='current':return False
    if evidence.get('origin')=='telegram_excerpt':
        return evidence.get('text')==doc['snapshot_text'] and str(evidence.get('title') or '')==doc['title'][:300]
    if evidence.get('origin')=='fetched_url_excerpt':
        return bool(doc['source_text']) and evidence.get('text')==doc['source_text'] and str(evidence.get('title') or '')==doc['source_title']
    return False



def _topic_context(item,term):
    """Require an observed expression in article context, not a webpage menu.

    This only controls topic support. Morphology observations and offsets remain
    unchanged, and names such as Facebook are never globally forbidden.
    """
    expressions={str(term.get('surface') or ''),str(term.get('label') or '')}-{''}
    def contains(value):
        folded=value.casefold()
        return any(re.search(r'(?<![A-Za-z0-9])'+re.escape(expr.casefold())+r'(?![A-Za-z0-9])',folded) for expr in expressions)
    def menu(value):
        # Short interface labels or button inventories, never the name itself.
        return bool(re.search(r'(공유하기|링크\s*복사|글자\s*크기|본문\s*바로가기|메뉴\s*(열기|닫기)|share\s+(on|via)|copy\s+link)',value,re.I))
    narrative=re.compile(r'(발표|공개|출시|개발|규제|금지|도입|확대|투자|제안|분석|연구|보도|결정|계획|중단|철회|시행|협력|예측|검토|배포|지원|승인|\b(?:announc|launch|releas|develop|report|introduc|invest|regulat|ban|propos|approv|suspend|research|said|says|will)\w*\b|发布|推出|开发|宣布|监管|投资|禁止|研究|计划)',re.I)
    sentence_form=re.compile(r'(?:다|됨|했음|예정|중이다|밝혔다)[.!?。]?$|\b(?:announced|launched|released|developed|reported|introduced|invested|regulated|banned|proposed|approved|suspended|said|says|will|has|have|is|are)\b|发布了|推出了|宣布|计划|已|将',re.I)
    for field in ('title','text'):
        for line in str(item.get(field) or '').splitlines():
            if contains(line) and not menu(line):
                # The curated Telegram/article fields are already URL-focused.
                return {'field':field,'quote':line[:700],'basis':'article_title_or_telegram_context'}
    source=item.get('source_context') or {}
    if source.get('status')!='fetched':return None
    title=str(source.get('title') or '')
    if contains(title) and not menu(title) and narrative.search(title):
        return {'field':'source_title','quote':title[:700],'basis':'retrieved_article_title'}
    copyright_notice=re.compile(r'자료.{0,30}(?:인용|보도).{0,80}출처.{0,80}(?:명시|표기)|출처.{0,40}(?:명시|표기).{0,30}(?:바랍니다|바랍|주세요)|무단.{0,12}(?:전재|재배포).{0,12}금지|all rights reserved|please.{0,40}(?:cite|credit).{0,40}(?:source|publication)',re.I)
    for line in re.split(r'\n|(?<=[.!?。])\s+',str(source.get('text') or '')):
        if not contains(line) or menu(line) or copyright_notice.search(line):continue
        # A bare name, navigation list or repeated footer cannot support a topic.
        if len(line.strip())>=18 and narrative.search(line) and sentence_form.search(line.strip()):
            return {'field':'source_text','quote':line[:700],'basis':'retrieved_narrative_context'}
    return None


def _model_topic_context(term, context):
    """Version-like tokens need model identity/context before topic promotion."""
    label=str(term.get('surface') or term.get('label') or '').strip()
    quote=(context or {}).get('quote','')
    if re.fullmatch(r'(?:under|over)[- ]?\d+(?:s|years?)?|(?:early|mid|late)[- ]?(?:19|20)\d{2}|(?:CA|NY|TX|FL|WA|IL|MA|VA|PA|NJ|OH)-\d{1,2}',label,re.I):
        return False
    if re.match(r'^(?:GPT|ChatGPT|Claude|Gemini|Llama|Qwen|DeepSeek|Kimi|Phi|Gemma)[- .]?\d',label,re.I):
        return True
    return bool(re.search(r'모델|언어모형|언어\s*모델|\b(?:model|LLM|GPT|Kimi|DeepSeek|Qwen|Claude|Gemini|Llama)\b|模型',quote,re.I))

def sync_store(db, items=None):
    """Caller-owned write sync; prepare morphology before beginning any writer work.

    Supplied items are a complete snapshot, not a partial ingestion batch.
    """
    if items is None:
        from improvement_selection import all_corpus_items
        items=all_corpus_items(db)
    unique_items={}
    for item in items:
        identity=_source_document(item)['id']
        previous=unique_items.get(identity)
        if previous is None or str(item.get('published_at') or '')>str(previous.get('published_at') or ''):
            unique_items[identity]=item
    items=list(unique_items.values())
    from morphology import keyword_records
    from keyword_index import keyword_record_id
    # A briefing may produce several URL documents with the same Telegram row ID.
    # Isolate morphology record keys by document, retaining original item text.
    morph_items=[dict(item,chat_id='intel-document',message_id=identity,item_index=0) for identity,item in unique_items.items()]
    indexed=keyword_records(db,morph_items)
    records={identity:indexed.get(keyword_record_id(morph),[]) for identity,morph in zip(unique_items,morph_items)}
    init_store(db)
    prior=_rows(db)
    docs={}
    item_by_doc={}
    for item in items:
        doc=_source_document(item)
        if doc['id'] not in docs or doc['collected_at']>docs[doc['id']]['collected_at']:
            docs[doc['id']]=doc;item_by_doc[doc['id']]=item
    for key,old in prior.items():
        if old['kind']=='document' and key not in docs:
            docs[key]=dict(old,status='removed')
    for did,raw in db.execute('SELECT document_id,payload_json FROM intel_source_attributions'):
        if did in docs:docs[did].update(json.loads(raw))
    claims={}
    semantic_observations=[]
    invalid_event_documents=set()
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    from evidence_corrections import correction_token, withdrawn_refs
    from graph_rag import validated_workflow_content
    corrections=correction_token(db)
    url_docs={d['url']:d for d in docs.values() if d['url']}
    snapshot_docs={}
    for doc in docs.values():
        if doc['status']=='current':snapshot_docs[(doc['snapshot_text'],doc['title'][:300])]=doc
    def evidence_document(e):
        url=canonical_url(e.get('url') or '')
        if url in url_docs:return url_docs[url]
        candidate=snapshot_docs.get((str(e.get('text') or ''),str(e.get('title') or '')))
        if candidate and not candidate['url'] and (not url or url==canonical_url(candidate.get('archive_url') or '')):
            return candidate
        return None
    cached_reviews={r[0]:(r[1],r[2]) for r in db.execute('SELECT run_id,input_hash,payload_json FROM intel_review_cache')}
    pending_reviews=[]
    if {'strategic_workflow_runs','strategic_workflow_artifacts'}<=tables:
        for run,status,error,raw in db.execute("SELECT r.id,r.status,r.error,a.payload_json FROM strategic_workflow_runs r JOIN strategic_workflow_artifacts a ON r.id=a.run_id WHERE a.stage='final'"):
            try:payload=json.loads(raw)
            except (TypeError,ValueError):continue
            fingerprint=_hash([VERSION,status,error,raw])
            cached=cached_reviews.get(run)
            if cached and cached[0]==fingerprint:
                content=json.loads(cached[1])
            else:
                content=validated_workflow_content(payload,run) if status=='complete' and not error else {}
                pending_reviews.append((run,fingerprint,_json(content)))
            if not content:continue
            evidence={e['id']:e for e in payload['evidence']}
            withdrawn=withdrawn_refs(payload,run,corrections)
            run_sources=defaultdict(list)
            for e in evidence.values():
                if e.get('origin')=='fetched_url_excerpt':
                    matched_doc=evidence_document(e)
                    if matched_doc:run_sources[matched_doc['id']].append(e)
            def resolve_refs(refs):
                parents=[];valid=bool(refs)
                for ref in refs:
                    e=evidence.get(ref,{})
                    doc=evidence_document(e)
                    if doc:parents.append(doc['id'])
                    source_matches=True
                    if doc and doc['url']:
                        fetched=run_sources.get(doc['id'],[])
                        source_matches=bool(doc['source_text'])==bool(fetched) and all(_matches(source,doc) for source in fetched)
                    valid=valid and ref not in withdrawn and _matches(e,doc) and source_matches
                return sorted(set(parents)),valid
            for claim in content['claims']:
                refs=claim['evidence_ids'];parents,valid=resolve_refs(refs)
                identity=_id('reviewed_claim',[run,claim['claim_id']])
                claims[identity]={'id':identity,'title':claim['title'],'detail':claim['detail'],
                    'uncertainty':claim['uncertainty'],'category':claim.get('category',''),
                    'workflow_run_id':run,'evidence_ids':refs,'document_ids':sorted(set(parents)),
                    'document_versions':{p:_hash({k:v for k,v in docs[p].items() if k not in ('version','kind')}) for p in parents},
                    'status':'current_reviewed' if valid else 'needs_review',
                    'review_hash':_hash(payload.get('verification')), 'analysis_basis':'reviewed_interpretation_not_fact_confirmation',
                    'reason':'current_source_match' if valid else 'source_changed_missing_or_explicitly_withdrawn'}
            observations=payload.get('event_observations', (payload.get('report') or {}).get('event_observations',[]))
            audit=payload.get('verification') or {}
            # Independent event audit is additional to the existing report gate.
            from event_observations import events_audit_issues
            event_audited=(isinstance(observations,list) and audit.get('event_hash')==_hash(observations)
                           and observations==(payload.get('report') or {}).get('event_observations')
                           and not events_audit_issues(audit,observations,payload['evidence']))
            if event_audited:
                for index,observation in enumerate(observations):
                    refs=observation.get('evidence_ids',[])
                    parents,valid=resolve_refs(refs)
                    valid=valid and set(refs)<=set(audit.get('checked_evidence_ids',[]))
                    if not valid:
                        invalid_event_documents.update(parents)
                        continue
                    semantic_observations.append(dict(observation,id=_id('event_observation',[run,index]),
                        workflow_run_id=run,document_ids=parents,verification='current_reviewed_observation',
                        source_urls=sorted({docs[p]['url'] for p in parents if docs[p]['url']}),
                        document_hashes={p:_hash(docs[p]) for p in parents},
                        basis='검토된 원문 해석; 독립적인 사건 발생 확인이나 인과 확정 아님'))
    for key,old in prior.items():
        if old['kind']=='claim' and key not in claims:
            claims[key]=dict(old,status='needs_review',reason='review_no_longer_admitted')
    assignments=dict(db.execute('SELECT document_id,event_id FROM intel_event_assignments'))
    event_docs=defaultdict(list)
    for doc in docs.values():
        if doc['status']!='current':continue
        # Exact same dated title is only a grouping candidate, not independent corroboration.
        key=['source',doc['original_source_url']] if doc['original_source_url'] else (
            ['title_date',_normal(doc['title']),doc['published_day']] if doc['title'] and doc['published_day'] and not re.search(r'브리핑|뉴스\s*모음|news\s*(digest|briefing)|daily\s*roundup',doc['title'],re.I) else ['document',doc['id']])
        event_docs[assignments.get(doc['id'],_id('event',key))].append(doc['id'])
    claims_by_doc=defaultdict(dict)
    for claim in claims.values():
        for did in claim['document_ids']:claims_by_doc[did][claim['id']]=claim
    observations_by_doc=defaultdict(dict)
    for observation in semantic_observations:
        for did in observation['document_ids']:observations_by_doc[did][observation['id']]=observation
    events={}
    for eid,members in event_docs.items():
        related=list({cid:c for did in members for cid,c in claims_by_doc[did].items()}.values())
        observed=list({oid:o for did in members for oid,o in observations_by_doc[did].items()}.values())
        events[eid]={'id':eid,'title':docs[members[0]]['title'],'document_ids':sorted(members),
            'claim_ids':sorted(c['id'] for c in related),'claim_states':{c['id']:_hash(c) for c in related},
            'source_versions':{d:_hash(docs[d]) for d in members},'status':'needs_review' if any(c['status']!='current_reviewed' for c in related) or set(members)&invalid_event_documents else 'candidate',
            'grouping':'manual' if any(d in assignments for d in members) else 'exact_source_or_dated_title_candidate',
            'independent_confirmation_count':sum(docs[d]['independent_confirmation']=='independent' for d in members) or None,
            'independence':'user_reviewed' if any(docs[d].get('attribution_basis') for d in members) else 'unknown',
            'semantic_observations':observed,
            **{field:(observed[0].get(field) or None) if len(observed)==1 else None for field in ('actor','action','target','outcome','occurred_at')},
            'countries_mentioned':[],
            'acting_countries':observed[0].get('actor_countries',[]) if len(observed)==1 else [],
            'affected_countries':observed[0].get('affected_countries',[]) if len(observed)==1 else [],
            'basis':'공통 제목·명시 원출처 기반 사건 후보; 동일 사건 확정·인과·독립 확인 아님'}
    events_by_doc=defaultdict(dict)
    for event in events.values():
        for did in event['document_ids']:events_by_doc[did][event['id']]=event
    event_hashes={eid:_hash(event) for eid,event in events.items()}
    aliases={}
    aliases_by_concept=defaultdict(list)
    for key,cid,language,surface in db.execute('SELECT alias_key,concept_id,language,surface FROM intel_aliases'):
        aliases[key]=cid
        aliases_by_concept[cid].append({'language':language,'surface':surface})
    concept_docs=defaultdict(set);surfaces=defaultdict(list);labels={};curated_concepts=set()
    topic_support=defaultdict(set);topic_contexts=defaultdict(list)
    for did,item in item_by_doc.items():
        for term in records.get(did,[]):
            if term.get('kind') not in {'noun_phrase','technical_dictionary','proper_noun','model_identifier'}:continue
            cid=aliases.get(_normal(term['label']),'concept:'+term['id'])
            concept_docs[cid].add(did);labels.setdefault(cid,term['label'])
            context=_topic_context(item,term)
            if context and (term.get('kind')!='model_identifier' or _normal(term['label']) in aliases or _model_topic_context(term,context)):
                topic_support[cid].add(did)
                if len(topic_contexts[cid])<6:topic_contexts[cid].append(dict(context,document_id=did))
                if term.get('kind') in {'technical_dictionary','model_identifier'} or _normal(term['label']) in aliases:curated_concepts.add(cid)
            expression={'document_id':did,'surface':term.get('surface',term['label']),'keyword_id':term['id'],'start':term.get('start'),'end':term.get('end'),'offset_basis':'morphology.item_text'}
            if expression not in surfaces[cid]:surfaces[cid].append(expression)
    topic_concepts={cid for cid,members in topic_support.items() if cid in curated_concepts or
                    (len(members)>=3 and len({docs[d]['published_day'] for d in members if docs[d]['published_day']})>=2)}
    changed=defaultdict(int)
    with db:
        db.executemany('INSERT OR REPLACE INTO intel_review_cache VALUES (?,?,?)',pending_reviews)
        coverage={'id':'collection:scope','channels':dict(Counter(d['channel'] for d in docs.values() if d['status']=='current')),
                  'languages':dict(Counter(d['language'] for d in docs.values() if d['status']=='current')),
                  'source_status':dict(Counter(d['source_status'] for d in docs.values() if d['status']=='current')),
                  'document_count':len(item_by_doc),'basis':'observed_snapshot_scope_not_collection_job_success'}
        _put(db,'collection_scope',coverage,'collection_scope_changed')
        for d in docs.values():changed['changed_documents']+=_put(db,'document',d,'source_snapshot_changed')
        for c in claims.values():
            changed_claim=_put(db,'claim',c,c['reason'],c['document_ids'])
            changed['changed_claims']+=changed_claim
            changed['invalidated_claims']+=bool(changed_claim and c['status']!='current_reviewed')
        for e in events.values():changed['changed_events']+=_put(db,'event',e,'dependent_sources_or_claims_changed',e['document_ids']+e['claim_ids'])
        for cid,members in concept_docs.items():
            alias_rows=aliases_by_concept[cid]
            concept={'id':cid,'label':prior.get(cid,{}).get('manual_label') or labels[cid],'aliases':alias_rows,'expressions':surfaces[cid],'document_ids':sorted(members),'status':'observed_concept','verification':'morphology_or_manual_alias_not_semantic_equivalence_proof'}
            if prior.get(cid,{}).get('manual_label'):concept['manual_label']=prior[cid]['manual_label']
            changed['changed_concepts']+=_put(db,'concept',concept,'observed_expressions_changed',sorted(members))
            if cid not in topic_concepts:continue
            members=topic_support[cid]
            related=list({eid:e for did in members for eid,e in events_by_doc[did].items()}.values())
            topic={'id':'topic:'+cid,'label':concept['label'],'concept_id':cid,'document_ids':sorted(members),
                'claim_ids':sorted({cid for did in members for cid,c in claims_by_doc[did].items() if c['status']=='current_reviewed'}),
                'supporting_contexts':topic_contexts[cid],'support_basis':'article_context_excluding_unsubstantiated_webpage_labels',
                'event_ids':sorted(e['id'] for e in related),'event_versions':{e['id']:event_hashes[e['id']] for e in related},
                'status':'needs_review' if any(e['status']=='needs_review' for e in related) else 'observed',
                'basis':'관측 표현·사건 후보 집계, 전략적 인과 또는 성과 확정 아님'}
            changed['changed_topics']+=_put(db,'topic',topic,'dependent_event_changed',[cid]+topic['event_ids'])
        current_ids=set(docs)|set(claims)|set(events)|set(concept_docs)|{'topic:'+c for c in topic_concepts}
        for key,old in prior.items():
            if key not in current_ids and old['kind'] in {'event','topic','concept'}:
                dormant=dict(old,status='dormant',document_ids=[])
                for field in ('event_ids','claim_ids','expressions'): 
                    if field in dormant:dormant[field]=[]
                changed['changed_'+old['kind']+'s']+=_put(db,old['kind'],dormant,'sources_no_longer_observed')
    counts={key:changed[key] for key in ('changed_documents','changed_claims','invalidated_claims','changed_events','changed_concepts','changed_topics')}
    return {**counts,'version':_version(db),'documents':len(item_by_doc),'method':VERSION}


def _version(db):
    return db.execute('SELECT COALESCE(MAX(seq),0) FROM intel_history').fetchone()[0]


def _one(params,key,default=''):
    value=(params or {}).get(key,default)
    return value[0] if isinstance(value,list) and value else value


def _momentum(db,params):
    docs=[d for d in _rows(db,'document').values() if d['status']=='current']
    topic=_one(params,'topic')
    if topic:
        allowed=set(_rows(db,'topic').get(topic,{}).get('document_ids',[]));docs=[d for d in docs if d['id'] in allowed]
    end=date.fromisoformat(_one(params,'date',date.today().isoformat()))
    events=list(_rows(db,'event').values());windows=[]
    for days in (7,28,90):
        buckets=[]
        for start,stop in [(end-timedelta(days=days-1),end),(end-timedelta(days=2*days-1),end-timedelta(days=days)),(end-timedelta(days=3*days-1),end-timedelta(days=2*days))]:
            selected=[d for d in docs if d['published_day'] and start.isoformat()<=d['published_day']<=stop.isoformat()]
            ids={d['id'] for d in selected}
            buckets.append({'urls':len(ids),'events':sum(bool(ids&set(e['document_ids'])) for e in events),
                'original_sources':len({d['original_source_url'] for d in selected if d['original_source_url']}),
                'original_source_unknown':sum(not d['original_source_url'] for d in selected),
                'channels':sorted({d['channel'] for d in selected if d['channel']}),'publishers':len({d['publisher'] for d in selected})})
        cur,prev,earlier=buckets
        keys=('urls','events','original_sources')
        growth={k:round((cur[k]-prev[k])/prev[k],4) if prev[k] else None for k in keys}
        previous_growth={k:round((prev[k]-earlier[k])/earlier[k],4) if earlier[k] else None for k in keys}
        coverage_changed=cur['channels']!=prev['channels'] or prev['channels']!=earlier['channels']
        acceleration={k:round(growth[k]-previous_growth[k],4) if growth[k] is not None and previous_growth[k] is not None and min(cur[k],prev[k],earlier[k])>=5 and not coverage_changed else None for k in keys}
        cautions=[]
        if any(not bucket['urls'] for bucket in buckets):cautions.append('zero_baseline_or_unobserved_period')
        if min(bucket['urls'] for bucket in buckets)<5:cautions.append('low_sample')
        if coverage_changed:cautions.append('channel_coverage_changed')
        windows.append({'days':days,'current':cur,'previous':prev,
            'earlier':earlier,'growth':growth,'previous_growth':previous_growth,'acceleration':acceleration,
            'acceleration_basis':'현재 증가율−직전 증가율; 세 기간 5건 이상 및 채널범위 동일일 때만 표시','cautions':cautions,
            'low_sample':min(cur['urls'],prev['urls'])<5,'coverage_changed':cur['channels']!=prev['channels'],
            'interpretation':'observed_corpus_change_not_technology_growth'})
    series=[]
    for offset in range(89,-1,-1):
        day=(end-timedelta(days=offset)).isoformat()
        selected=[d for d in docs if d['published_day']==day]
        ids={d['id'] for d in selected}
        series.append({'day':day,'urls':len(ids),'events':sum(bool(ids&set(e['document_ids'])) for e in events),
                       'original_sources':len({d['original_source_url'] for d in selected if d['original_source_url']})})
    return {'windows':windows,'series':series,'active_days_90':sum(row['urls']>0 for row in series),'undated_documents':sum(not d['published_day'] for d in docs),
            'basis':'기사 날짜가 확인된 현재 수집 자료만 집계; 원출처 미상 제외, 독립 확인 미상',
            'collection_history_status':'channel_coverage_proxy_only_no_causal_attribution','version':_version(db)}


def get_document(db, document_id):
    row=db.execute("SELECT payload_json FROM intel_entities WHERE kind='document' AND id=?",(document_id,)).fetchone()
    return json.loads(row[0]) if row else None


def query_store(db,view,params=None):
    params=params or {}
    if view in {'document','claim','event','topic','concept'}:
        row=db.execute('SELECT payload_json FROM intel_entities WHERE kind=? AND id=?',(view,_one(params,'id'))).fetchone()
        if not row:return None
        value=json.loads(row[0])
        excluded=db.execute('SELECT excluded FROM intel_exclusions WHERE id=?',(value['id'],)).fetchone()
        value['excluded']=bool(excluded and excluded[0])
        return value
    if view=='momentum':return _momentum(db,params)
    if view=='coverage':
        docs=list(_rows(db,'document').values())
        return {'documents':len(docs),'current':sum(d['status']=='current' for d in docs),'source_status':dict(__import__('collections').Counter(d['source_status'] for d in docs)),
            'languages':dict(Counter(d['language'] for d in docs)),
            'countries_mentioned':dict(Counter(str(c) for d in docs for c in d.get('countries_mentioned',[]))),
            'collection_scope_history':[{'version':r[0],'recorded_at':r[1],'scope':json.loads(r[2])} for r in db.execute("SELECT version,created_at,payload_json FROM intel_history WHERE id='collection:scope' ORDER BY version DESC LIMIT 30")],
            'last_collected_at':max((d['collected_at'] for d in docs),default=''),
            'last_telegram_published_at':max((d.get('telegram_published_at','') for d in docs),default=''),'independent_confirmation':'unknown','version':_version(db)}
    if view=='history':
        rows=[dict(id=r[0],version=r[1],reason=r[2],created_at=r[3],snapshot=json.loads(r[4])) for r in db.execute('SELECT id,version,reason,created_at,payload_json FROM intel_history WHERE id=? ORDER BY version',(_one(params,'id'),))]
    else:
        kind={'documents':'document','claims':'claim','events':'event','topics':'topic','concepts':'concept'}.get(view)
        if not kind:raise ValueError('Unknown strategic store view')
        rows=list(_rows(db,kind).values())
        if kind=='topic' and str(_one(params,'include_dormant','')).casefold() not in {'1','true','yes'} and _one(params,'status')!='dormant':
            rows=[row for row in rows if row.get('status')!='dormant']
        if kind=='document':
            event_id=_one(params,'event_id')
            if event_id:
                event=query_store(db,'event',{'id':event_id})
                allowed=set((event or {}).get('document_ids',[]))
                rows=[r for r in rows if r['id'] in allowed]
        excluded={r[0] for r in db.execute('SELECT id FROM intel_exclusions WHERE excluded=1')}
        if not _one(params,'include_excluded',False):rows=[r for r in rows if r['id'] not in excluded and r.get('concept_id') not in excluded]
        for row in rows:row['excluded']=row['id'] in excluded
        for field in ('id','status'):
            value=_one(params,field)
            if value:rows=[r for r in rows if r.get(field)==value]
        query=_normal(_one(params,'q'))
        if query:rows=[r for r in rows if query in _normal(r.get('title') or r.get('label') or '')]
    page=max(1,int(_one(params,'page',1)));size=max(1,min(100,int(_one(params,'page_size',20))))
    rows.sort(key=lambda r:r['id'])
    for row in rows:
        row.update(document_count=len(row.get('document_ids',[])),event_count=len(row.get('event_ids',[])),
                   evidence_count=len(row.get('evidence_ids',[])),summary=row.get('detail') or row.get('basis') or '')
    return {'items':rows[(page-1)*size:page*size],'total':len(rows),'page':page,'page_size':size,'total_pages':(len(rows)+size-1)//size,'version':_version(db)}


def mutate_store(db,resource,payload):
    reason=str(payload.get('reason') or '').strip()
    if not reason:raise ValueError('A review reason is required')
    init_store(db)
    with db:
        if resource in ('event_merge','event_split'):
            event_ids=list(dict.fromkeys(payload.get('event_ids') or payload.get('ids') or ([payload['id']] if payload.get('id') else [])));events=_rows(db,'event')
            if not event_ids or any(e not in events for e in event_ids):raise ValueError('Existing event_ids required')
            members=sorted({d for e in event_ids for d in events[e]['document_ids']})
            selected=payload.get('document_ids') or members
            if not selected or not set(selected)<=set(members):raise ValueError('Split documents must belong to selected events')
            target=_id('event_manual',[sorted(event_ids),sorted(selected),reason,resource])
            for did in selected:db.execute('INSERT OR REPLACE INTO intel_event_assignments VALUES (?,?,?)',(did,target,reason))
            action={'id':_id('event_action',[target,_version(db)]),'operation':resource,'event_ids':event_ids,'document_ids':selected,'target_event_id':target,'reason':reason}
            _put(db,'operation',action,reason)
        elif resource in ('concept_alias','concept_split'):
            if resource=='concept_split' and payload.get('aliases'):
                names=[str(v).strip() for v in payload['aliases'] if str(v).strip()]
                if not names:raise ValueError('Nonempty aliases required')
                existing={r[0]:r for r in db.execute('SELECT alias_key,concept_id,language,surface FROM intel_aliases')}
                old_cid=payload.get('concept_id')
                if any(_normal(v) not in existing or existing[_normal(v)][1]!=old_cid for v in names):raise ValueError('Aliases must belong to the original concept')
                target=_id('concept_manual',[sorted(names),reason])
                for name in names:
                    prior_alias=existing[_normal(name)]
                    db.execute('UPDATE intel_aliases SET concept_id=?,reason=? WHERE alias_key=?',(target,reason,_normal(name)))
                action={'id':_id('alias_action',[target,_version(db)]),'operation':resource,'concept_id':target,'previous_concept_id':old_cid,'aliases':names,'label':payload.get('label') or names[0],'reason':reason}
                _put(db,'operation',action,reason)
                _put(db,'concept',{'id':target,'label':action['label'],'manual_label':action['label'],'aliases':[{'surface':name,'language':existing[_normal(name)][2]} for name in names],'document_ids':[],'expressions':[],'status':'pending_observation'},reason)
                return dict(action,version=_version(db),requires_sync=True)
            surface=str(payload.get('surface') or payload.get('alias') or '').strip();language=payload.get('language')
            if not surface or language not in ('ko','en','zh'):raise ValueError('surface and ko/en/zh language required')
            cid=str(payload.get('concept_id') or '')
            if resource=='concept_split':cid=_id('concept_manual',[_normal(surface),language,reason])
            elif cid not in _rows(db,'concept'):raise ValueError('Existing concept_id required')
            db.execute('INSERT OR REPLACE INTO intel_aliases VALUES (?,?,?,?,?)',(_normal(surface),cid,language,surface,reason))
            action={'id':_id('alias_action',[cid,surface,_version(db)]),'operation':resource,'concept_id':cid,'surface':surface,'language':language,'reason':reason}
            _put(db,'operation',action,reason)
        elif resource == 'source_attribution':
            identity=str(payload.get('document_id') or '')
            if not get_document(db,identity):raise ValueError('Existing document_id required')
            independence=payload.get('independence','unknown')
            if independence not in ('unknown','independent','derived'):raise ValueError('Invalid independence')
            raw_url=str(payload.get('original_source_url') or '')
            if raw_url and urlsplit(raw_url).scheme not in ('http','https'):raise ValueError('HTTP source URL required')
            url=canonical_url(raw_url)
            attribution={'original_source_url':url,'independent_confirmation':independence,
                'origin_status':'user_reviewed' if url else 'unknown','attribution_basis':'explicit_user_review',
                'attribution_reason':reason}
            db.execute('INSERT OR REPLACE INTO intel_source_attributions VALUES (?,?)',(identity,_json(attribution)))
            action={'id':_id('attribution_action',[identity,_version(db)]),'document_id':identity,'operation':resource,**attribution}
            _put(db,'operation',action,reason)
        elif resource in ('exclude','concept_exclude','event_exclude','topic_exclude'):
            identity=str(payload.get('id') or '')
            if identity not in _rows(db):raise ValueError('Existing id required')
            if not isinstance(payload.get('excluded'),bool):raise ValueError('excluded must be boolean')
            db.execute('INSERT OR REPLACE INTO intel_exclusions VALUES (?,?,?)',(identity,int(payload['excluded']),reason))
            action={'id':_id('exclude_action',[identity,_version(db)]),'target_id':identity,'operation':'exclude','excluded':payload['excluded'],'reason':reason}
            _put(db,'operation',action,reason)
        else:raise ValueError('Unknown strategic store mutation')
    return dict(action,version=_version(db),requires_sync=True)
