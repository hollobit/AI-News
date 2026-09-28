"""Bounded original-source snapshots for the wiki; never ingest another wiki."""
import json
from link_groups import canonical_url
from bulk_baseline import digest
from strategy_trends import LENSES, matched_terms

TOPICS = {x['id']: x for x in LENSES if x['id'] in ('medical', 'public_ax', 'sovereign')}
VERSION = 'news-wiki-1'


def exists(db, table):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone())


def news_source(db, row):
    row = dict(row)
    url = canonical_url(row.get('source_url') or '')
    text = '\n'.join(str(row.get(k) or '') for k in ('title', 'text', 'excerpt')).strip()
    key = [row['chat_id'], row['message_id'], row['item_index']]
    dependency = {'kind': 'news', 'key': key, 'token': digest(row)}
    evidence = {'id': 'news:' + digest(url or text)[:24], 'title': row.get('title') or '',
                'text': text[:3500], 'source_url': url, 'day': row.get('day') or '',
                'origin': 'telegram_excerpt', 'scope': '수집 뉴스 발췌; 발행자의 사실성 독립 확인 아님',
                'dependency': dependency}
    if url and exists(db, 'source_health'):
        source = db.execute('''SELECT v.title,v.text,v.scope,h.hash,h.last_status FROM source_health h
            LEFT JOIN source_versions v ON v.url=h.url AND v.hash=h.hash WHERE h.url=?''', (url,)).fetchone()
        dependency['source_url'] = url
        dependency['source_state'] = [source['hash'], source['last_status']] if source else None
        if source and source['last_status'] == 'fetched' and source['text']:
            evidence.update(text=source['text'][:5000], title=source['title'] or evidence['title'],
                            origin='fetched_url_excerpt', scope='확보 원문 앞부분 최대 5,000자; 전문 분석 아님')
    return evidence


def paper_token(db, paper_id):
    """Validate only the cited paper, not the entire research corpus on every GET."""
    if not exists(db, 'arxiv_papers') or not exists(db, 'arxiv_paper_analyses'):
        return None
    metadata = db.execute('SELECT metadata_json,status FROM arxiv_papers WHERE paper_id=?', (paper_id,)).fetchone()
    analysis = db.execute('SELECT * FROM arxiv_paper_analyses WHERE paper_id=?', (paper_id,)).fetchone()
    if not metadata or not analysis:
        return None
    from paper_graph import validated_paper_analysis
    item = json.loads(metadata['metadata_json'])
    item.update(paper_id=paper_id, metadata_status=metadata['status'])
    item.setdefault('metadata_version', None)
    for field in ('abstract', 'primary_category', 'published', 'updated', 'doi', 'journal_ref'):
        item.setdefault(field, '')
    for field in ('authors', 'categories'):
        item.setdefault(field, [])
    row = dict(analysis)
    return digest(row) if validated_paper_analysis(row, item) else None


def current(db, evidence, memo=None):
    """Deletion, failed latest fetch, metadata and reviewed-result changes invalidate."""
    memo = {} if memo is None else memo
    for source in evidence:
        dep = source['dependency']
        if dep['kind'] == 'news':
            row = db.execute('SELECT * FROM articles WHERE chat_id=? AND message_id=? AND item_index=?', dep['key']).fetchone()
            if not row or digest(dict(row)) != dep['token']:
                return False
            if dep.get('source_url'):
                health = db.execute('SELECT hash,last_status FROM source_health WHERE url=?', (dep['source_url'],)).fetchone()
                if (list(health) if health else None) != dep['source_state']:
                    return False
        elif dep['kind'] == 'paper':
            key = ('paper', dep['key'])
            if key not in memo:
                memo[key] = paper_token(db, dep['key'])
            if memo[key] != dep['token']:
                return False
        else:
            return False
    return True


def collect(db, topic, previous=None):
    from wiki_registry import topics, config_token
    lens = topics(db)[topic]
    sources = {}
    if exists(db, 'articles'):
        # SQL is a shortlist only; the established boundary-aware matcher decides.
        expression = "lower(COALESCE(title,'')||' '||COALESCE(text,'')||' '||COALESCE(excerpt,''))"
        clauses = ' OR '.join(expression + ' LIKE ?' for _ in lens['terms'])
        rows = db.execute('SELECT * FROM articles WHERE ' + clauses + ' ORDER BY day DESC,message_id DESC,item_index LIMIT 300',
                          ['%' + t.casefold() + '%' for t in lens['terms']])
        for row in rows:
            if not matched_terms(dict(row), lens['terms']):
                continue
            source = news_source(db, row)
            sources.setdefault(source['id'], source)
            if len(sources) >= 12:
                break
        if lens['historical']:
            # Oldest matching documents add a bounded historical contrast, not
            # a claim of exhaustive coverage. Keep exact source validation.
            older = db.execute('SELECT * FROM articles WHERE ' + clauses + ' ORDER BY day,message_id,item_index',
                               ['%' + t.casefold() + '%' for t in lens['terms']])
            added = 0
            for row in older:
                if not matched_terms(dict(row), lens['terms']):
                    continue
                source = news_source(db, row)
                if source['id'] not in sources:
                    sources[source['id']] = source
                    added += 1
                if added >= 8:
                    break
    if exists(db, 'arxiv_paper_analyses'):
        from paper_graph import paper_sources
        count = 0
        for item in reversed(paper_sources(db)[0]):
            result = json.loads(item['row']['result_json'])
            raw = [e for e in result['evidence'] if e['origin'] in ('arxiv_abstract', 'scholarly_index_abstract')]
            if not raw or not matched_terms(raw[0], lens['terms']):
                continue
            e = raw[0]
            source = dict(id='paper:' + item['id'], title=e['title'], text=e['text'],
                          source_url=e['source_url'], day=e.get('published', '')[:10], origin=e['origin'],
                          scope='검토 논문의 제목·초록; 전문·제품 성능·임상 검증 아님',
                          dependency={'kind': 'paper', 'key': item['id'], 'token': digest(item['row'])})
            sources[source['id']] = source
            count += 1
            if count >= 3:
                break
    # Carry still-current older sources forward; history retains sources outside
    # this bounded synthesis instead of pretending the current page covers all news.
    memo = {}
    for source in (previous or {}).get('evidence', []):
        if len(sources) >= 24:
            break
        if source['id'] not in sources and current(db, [source], memo):
            sources[source['id']] = source
    evidence = sorted(sources.values(), key=lambda e: e['id'])
    token = config_token(lens)
    return {'topic': topic, 'title': lens['name'], 'version': VERSION, 'evidence': evidence, 'config_hash': token,
            'input_hash': digest([VERSION, topic, evidence, token]),
            'scope': '최근 일치 뉴스 최대 12개·검토 논문 초록 최대 3개' + ('·과거 일치 뉴스 최대 8개' if lens['historical'] else '') + '와 이전 근거를 합쳐 최대 24개. 전체 뉴스의 종합이나 사실성 보증이 아닙니다.'}


def collect_question(db, topic, question):
    from wiki_registry import topics, config_token
    wanted = set(json.loads(question['urls_json']))
    evidence = {}
    if exists(db,'articles'):
        for row in db.execute('SELECT * FROM articles ORDER BY day DESC,message_id DESC,item_index'):
            if canonical_url(row['source_url'] or '') in wanted:
                source = news_source(db,row)
                evidence.setdefault(source['id'],source)
    if exists(db,'arxiv_paper_analyses'):
        from paper_graph import paper_sources
        for paper in paper_sources(db)[0]:
            result = json.loads(paper['row']['result_json'])
            for raw in result['evidence']:
                if raw['origin'] not in ('arxiv_abstract','scholarly_index_abstract') or canonical_url(raw['source_url']) not in wanted:continue
                identity = 'paper:'+paper['id']
                evidence[identity] = dict(id=identity,title=raw['title'],text=raw['text'],source_url=raw['source_url'],
                    day=raw.get('published','')[:10],origin=raw['origin'],scope='검토된 논문 초록; 전문 아님',
                    dependency=dict(kind='paper',key=paper['id'],token=digest(paper['row'])))
    config = topics(db)[topic]
    evidence = sorted(evidence.values(),key=lambda e:e['id'])[:24]
    token = config_token(config)
    return dict(topic=topic,title=config['name'],question=question['question'],version=VERSION,
        evidence=evidence,config_hash=token,input_hash=digest([VERSION,topic,evidence,token]),
        scope='저장 요청한 질문의 원출처 최대 24개를 다시 종합·독립 검토한 페이지입니다. 이전 모델 답변은 원근거로 사용하지 않습니다.')
