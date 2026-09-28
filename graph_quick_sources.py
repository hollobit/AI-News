"""Bounded cold-start source lookup; never presented as reviewed analysis."""
import sqlite3
import time
from evidence_search import terms, query_anchors, alias_patterns
from keyword_index import document_id


def cold_answer(path, question, ids, params, budget=2):
    # A node/lens scope cannot be inferred without the graph: never broaden it.
    if ids or set(params)-{'date','topic','channel','kind'}:return None
    started=time.monotonic();query=set(terms(question));anchors=query_anchors(question)
    if not query:return None
    words={t for t in query if not t.startswith('concept:')}
    # Curated aliases are evaluated by the tokenizer below. SQL is only a cheap
    # shortlist; retain aliases as LIKE terms so Korean queries find English text.
    from morphology import PHRASES
    from evidence_search import ALIASES
    for label, aliases in {**PHRASES,**ALIASES}.items():
        if 'concept:'+label.casefold() in query:words.update(a.casefold() for a in aliases)
    words=sorted(words,key=lambda w:(-len(w),w))[:24]
    if not words:return None
    clauses=[];values=[]
    for key,value in params.items():
        value=value[0] if isinstance(value,list) else value
        if value=='all':continue
        clauses.append(('a.day' if key=='date' else 'n.channel' if key=='channel' else 'a.'+key)+'=?');values.append(value)
    db=sqlite3.connect(path,timeout=.15);db.row_factory=sqlite3.Row
    db.set_progress_handler(lambda:int(time.monotonic()-started>budget),1000)
    candidates=[]
    try:
        indexed=db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_source_search'").fetchone()
        searchable=[w for w in words if len(w)>=3]
        source='articles a'
        if indexed and searchable:
            source='graph_source_search JOIN articles a ON a.rowid=graph_source_search.rowid'
            clauses.append('graph_source_search MATCH ?')
            values.append(' OR '.join('"'+w.replace('"','""')+'"' for w in searchable))
        else:
            text="lower(COALESCE(a.title,'')||' '||COALESCE(a.excerpt,'')||' '||COALESCE(a.text,''))"
            clauses.append('('+' OR '.join(text+" LIKE ?" for _ in words)+')')
            values += ['%'+w+'%' for w in words]
        rows=db.execute('SELECT a.*,n.channel FROM '+source+' JOIN news n ON a.chat_id=n.chat_id AND a.message_id=n.message_id WHERE '+' AND '.join(clauses)+' ORDER BY a.rowid DESC LIMIT 300',values)
        for row in rows:
            if time.monotonic()-started>budget:break
            item=dict(row);body=(item.get('text') or item.get('excerpt') or '')[:6000]
            tokens=set(terms((item.get('title') or '')+' '+body))
            overlap=tokens&query
            if not overlap or anchors and not anchors&tokens:continue
            candidates.append((len(overlap)+2*len(set(terms(item.get('title')))&query),item,body))
    except sqlite3.OperationalError:
        # An interrupted query may still have yielded valid source rows.
        pass
    finally:db.close()
    selected=[];seen=set()
    for score,item,body in sorted(candidates,key=lambda r:-r[0]):
        doc=document_id(item)
        if doc in seen:continue
        seen.add(doc)
        selected.append(dict(id='cold-source:'+doc,document_id=doc,title=item.get('title') or '',text=body,
            source_url=item.get('source_url') or '',day=item.get('day') or '',origin='stored_news_excerpt'))
        if len(selected)>=6:break
    if not selected:return None
    from graph_questions import passage
    return dict(phase='evidence',answer='관련 보관 뉴스의 원문 발췌입니다. 그래프 검색과 추가 분석·검토를 진행 중입니다.',
        claims=[dict(text=passage(e,question),evidence_ids=[e['id']],kind='source_excerpt') for e in selected[:4]],
        evidence=selected,reviewed_claims=[],selected_nodes=[],selected_edges=[],
        limitations=['빠른 검색은 최근 저장된 일치 뉴스 최대 300건을 대상으로 합니다. 발췌는 출처의 주장이며 검증된 답변이 아닙니다.'],
        verification={'method':'bounded_cold_source_lookup','question_answer_reviewed':False})


def setup_source_search(path):
    """Incremental trigram index for cold requests; normal SQLite triggers keep it current."""
    with sqlite3.connect(path,timeout=10) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='articles'").fetchone():return False
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='graph_source_search'").fetchone():return True
        try:
            db.execute("CREATE VIRTUAL TABLE graph_source_search USING fts5(title,excerpt,text,content='articles',content_rowid='rowid',tokenize='trigram')")
            db.execute("INSERT INTO graph_source_search(graph_source_search) VALUES ('rebuild')")
            db.execute('''CREATE TRIGGER graph_source_search_insert AFTER INSERT ON articles BEGIN
                INSERT INTO graph_source_search(rowid,title,excerpt,text) VALUES (new.rowid,new.title,new.excerpt,new.text); END''')
            db.execute('''CREATE TRIGGER graph_source_search_delete AFTER DELETE ON articles BEGIN
                INSERT INTO graph_source_search(graph_source_search,rowid,title,excerpt,text) VALUES ('delete',old.rowid,old.title,old.excerpt,old.text); END''')
            db.execute('''CREATE TRIGGER graph_source_search_update AFTER UPDATE ON articles BEGIN
                INSERT INTO graph_source_search(graph_source_search,rowid,title,excerpt,text) VALUES ('delete',old.rowid,old.title,old.excerpt,old.text);
                INSERT INTO graph_source_search(rowid,title,excerpt,text) VALUES (new.rowid,new.title,new.excerpt,new.text); END''')
        except sqlite3.OperationalError:
            db.rollback();return False
    return True
