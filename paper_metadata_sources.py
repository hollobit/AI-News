"""Provenance-preserving metadata import and alternative scholarly providers."""
import json,re,sqlite3,time,os
from datetime import datetime,timezone,timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from xml.etree import ElementTree as ET
from arxiv_papers import parse_arxiv_id

SECONDARY={'openalex','semantic_scholar'}
def now():return datetime.now(timezone.utc).isoformat()
def init(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS paper_metadata_versions(paper_id TEXT,provider TEXT,hash TEXT,payload TEXT,at TEXT,PRIMARY KEY(paper_id,provider,hash));
    CREATE TABLE IF NOT EXISTS paper_provider_state(provider TEXT PRIMARY KEY,next_at REAL,error TEXT,cursor TEXT,since TEXT,checked_at TEXT);
    CREATE TABLE IF NOT EXISTS paper_provider_attempts(provider TEXT,paper_id TEXT,day TEXT,status TEXT,PRIMARY KEY(provider,paper_id,day));''')
def save(db,data):
    from source_store import digest
    identity=data['paper_id'];provider=data['metadata_source']
    if not parse_arxiv_id(identity) or not data.get('title') or not data.get('abstract') or len(data['abstract'])>24000:raise ValueError('제목·초록·논문 ID 확인 필요')
    row=db.execute('SELECT metadata_json,status FROM arxiv_papers WHERE paper_id=?',(identity,)).fetchone()
    if not row:return False
    previous=json.loads(row[0] or '{}')
    data=dict(data,retrieved_at=now())
    signature=digest(json.dumps({k:v for k,v in data.items() if k!='retrieved_at'},sort_keys=True,ensure_ascii=False))
    db.execute('INSERT OR IGNORE INTO paper_metadata_versions VALUES(?,?,?,?,?)',(identity,provider,signature,json.dumps(data,ensure_ascii=False),now()))
    # Secondary indexes must never replace available official metadata.
    if row[1]=='fetched' and previous.get('metadata_source','arxiv_atom_api') not in SECONDARY and provider in SECONDARY:return False
    if previous.get('metadata_version') and data.get('metadata_version') and previous['metadata_version']>data['metadata_version']:return False
    if row[1]=='fetched' and previous.get('metadata_source')==provider and previous.get('updated','')>data.get('updated',''):return False
    if all(previous.get(k)==v for k,v in data.items() if k!='retrieved_at') and row[1]=='fetched':return False
    db.execute("UPDATE arxiv_papers SET metadata_json=?,status='fetched',error='',fetched_at=? WHERE paper_id=?",(json.dumps(data,ensure_ascii=False),now(),identity))
    return True

def date_value(value):
    if not value:return ''
    try:return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError):
        try:return datetime.fromisoformat(value.replace('Z','+00:00')).isoformat()
        except ValueError:return ''

def snapshot_record(row):
    identity=parse_arxiv_id(row.get('id',''))
    if not identity:raise ValueError('배포본 논문 ID 오류')
    versions=row.get('versions') or [];latest=versions[-1] if versions else {}
    categories=str(row.get('categories','')).split()
    return {'paper_id':identity['paper_id'],'title':re.sub(r'\s+',' ',row.get('title','')).strip(),'abstract':row.get('abstract','').strip(),
        'authors':[' '.join(str(v) for v in [a[1],a[0],*a[2:]] if v).strip() for a in row.get('authors_parsed',[]) if len(a)>=2],
        'categories':categories,'primary_category':categories[0] if categories else '',
        'published':date_value(versions[0].get('created','')) if versions else '', 'updated':date_value(latest.get('created','')),
        'doi':row.get('doi') or '', 'journal_ref':row.get('journal-ref') or '',
        'metadata_version':int(latest['version'].lstrip('v')) if re.fullmatch(r'v\d+',latest.get('version','')) else None,
        'metadata_source':'arxiv_kaggle','metadata_record_url':'https://www.kaggle.com/datasets/Cornell-University/arxiv',
        'evidence_scope':'official_snapshot_metadata_and_abstract','source_url':'https://arxiv.org/abs/'+identity['paper_id']}

def openalex_record(row,identity):
    links=[l.get('landing_page_url','') for l in row.get('locations',[])]+[row.get('doi') or '']
    linked=any((p:=parse_arxiv_id(url)) and p['paper_id']==identity for url in links)
    linked=linked or (row.get('doi') or '').casefold()=='https://doi.org/10.48550/arxiv.'+identity.casefold()
    if not linked:raise ValueError('OpenAlex 논문 식별자가 요청 ID와 다릅니다.')
    positions={}
    for word,indices in (row.get('abstract_inverted_index') or {}).items():
        for i in indices:
            if type(i) is not int or not 0<=i<24000 or (i in positions and positions[i]!=word):raise ValueError('초록 위치 오류')
            positions[i]=word
    if not positions or sorted(positions)!=list(range(len(positions))):raise ValueError('OpenAlex 초록 미확보 또는 누락')
    return {'paper_id':identity,'title':row.get('title',''),'abstract':' '.join(positions[i] for i in range(len(positions))),
        'authors':[a.get('author',{}).get('display_name','') for a in row.get('authorships',[])],
        'categories':[],'primary_category':'','published':row.get('publication_date') or '', 'updated':'', 'doi':row.get('doi') or '', 'journal_ref':'','metadata_version':None,
        'metadata_source':'openalex','metadata_record_url':row['id'],'evidence_scope':'secondary_index_metadata_and_abstract','source_url':'https://arxiv.org/abs/'+identity}

def semantic_record(row,identity):
    if row.get('externalIds',{}).get('ArXiv')!=identity:raise ValueError('Semantic Scholar 논문 ID 불일치')
    return {'paper_id':identity,'title':row.get('title',''),'abstract':row.get('abstract') or '',
        'authors':[a.get('name','') for a in row.get('authors',[])],'categories':[],'primary_category':'',
        'published':row.get('publicationDate') or '', 'updated':'','doi':row.get('externalIds',{}).get('DOI') or '', 'journal_ref':'','metadata_version':None,
        'metadata_source':'semantic_scholar','metadata_record_url':'https://www.semanticscholar.org/paper/'+row['paperId'],
        'evidence_scope':'secondary_index_metadata_and_abstract','source_url':'https://arxiv.org/abs/'+identity}

def parse_oai(body):
    if len(body)>20_000_000 or re.search(br'<!DOCTYPE|<!ENTITY',body,re.I):raise ValueError('OAI XML 크기/엔티티 오류')
    root=ET.fromstring(body);ns='{http://www.openarchives.org/OAI/2.0/}'
    error=root.find(ns+'error')
    if error is not None and error.get('code')!='noRecordsMatch':raise ValueError('OAI '+str(error.get('code')))
    records=[];deleted=[]
    for record in root.findall('.//'+ns+'record'):
        header=record.find(ns+'header');identity=(header.findtext(ns+'identifier','') if header is not None else '').replace('oai:arXiv.org:','')
        if header is not None and header.get('status')=='deleted':deleted.append(identity);continue
        metadata=record.find(ns+'metadata')
        if metadata is None or not list(metadata):continue
        raw=list(metadata)[0]
        def find(name):return next((c for c in raw if c.tag.split('}')[-1]==name),None)
        def value(name):
            el=find(name);return ''.join(el.itertext()).strip() if el is not None else ''
        versions=[]
        for v in raw:
            if v.tag.split('}')[-1]=='version':
                children={c.tag.split('}')[-1]:c.text or '' for c in v};versions.append({'version':v.get('version',''),'created':children.get('date','')})
        parsed=snapshot_record({'id':value('id') or identity,'title':value('title'),'abstract':value('abstract'),'categories':value('categories'),'doi':value('doi'),'journal-ref':value('journal-ref'),'versions':versions})
        if parsed['paper_id']!=identity:raise ValueError('OAI 헤더/레코드 ID 불일치')
        authors=value('authors')
        parsed['authors']=[authors] if authors else []
        parsed.update(metadata_source='arxiv_oai',metadata_record_url='https://oaipmh.arxiv.org/oai?'+urlencode({'verb':'GetRecord','identifier':'oai:arXiv.org:'+identity,'metadataPrefix':'arXivRaw'}),evidence_scope='official_metadata_and_abstract')
        records.append(parsed)
    token=root.find('.//'+ns+'resumptionToken')
    return records,deleted,token.text if token is not None and token.text else '',root.findtext(ns+'responseDate','')[:10]

def fetch(url,headers=None,limit=20_000_000):
    req=Request(url,headers={'User-Agent':'HaruNewsPaperIndex/1.0 (research metadata reader)',**(headers or {})})
    with urlopen(req,timeout=30) as response:body=response.read(limit+1)
    if len(body)>limit:raise ValueError('메타데이터 응답 크기 초과')
    return body

class AlternateMetadata:
    def __init__(self,path):
        self.path=str(path)
        with self.db() as db:
            init(db)
            for provider in ('arxiv_oai','openalex','semantic_scholar'):
                db.execute('INSERT OR IGNORE INTO paper_provider_state VALUES(?,0,\'\',\'\',?,\'\')',(provider,(datetime.now(timezone.utc)-timedelta(days=7)).date().isoformat()))
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row;return db
    def status(self):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM paper_provider_state')]
    def tick(self):
        for provider in ('openalex','arxiv_oai','semantic_scholar'):
            with self.db() as db:state=dict(db.execute('SELECT * FROM paper_provider_state WHERE provider=?',(provider,)).fetchone())
            if state['next_at']>time.time():continue
            try:
                if provider=='arxiv_oai':self.oai(state)
                else:self.index(provider)
            except Exception as e:
                delay=3600
                if isinstance(e,HTTPError):
                    if e.code in (401,403,406):delay=86400
                    elif e.code==404:delay=30
                    try:delay=max(delay,float(e.headers.get('Retry-After','0')))
                    except ValueError:
                        try:delay=max(delay,parsedate_to_datetime(e.headers.get('Retry-After')).timestamp()-time.time())
                        except (TypeError,ValueError):pass
                    message='HTTP '+str(e.code)
                else:
                    message=type(e).__name__+': '+str(e)[:180]
                    if isinstance(e,ValueError):delay=30
                with self.db() as db:
                    if provider=='arxiv_oai' and 'badResumptionToken' in message:db.execute("UPDATE paper_provider_state SET cursor='' WHERE provider=?",(provider,))
                    db.execute('UPDATE paper_provider_state SET next_at=?,error=?,checked_at=? WHERE provider=?',(time.time()+delay,message,now(),provider))
            return # At most one remote request per scheduler tick.
    def index(self,provider):
        today=now()[:10]
        with self.db() as db:
            used=db.execute('SELECT count(*) FROM paper_provider_attempts WHERE provider=? AND day=?',(provider,today)).fetchone()[0]
            row=db.execute("""SELECT p.paper_id FROM arxiv_papers p WHERE p.status!='fetched' AND NOT EXISTS
                (SELECT 1 FROM paper_provider_attempts a WHERE a.provider=? AND a.paper_id=p.paper_id AND a.day=?) ORDER BY p.paper_id LIMIT 1""",(provider,today)).fetchone()
            if used>=100 or not row:
                db.execute('UPDATE paper_provider_state SET next_at=? WHERE provider=?',(time.time()+3600,provider));return
            identity=row[0];db.execute('INSERT INTO paper_provider_attempts VALUES(?,?,?,?)',(provider,identity,today,'requested'))
        if provider=='openalex':
            key=os.environ.get('OPENALEX_API_KEY');headers={'Authorization':'Bearer '+key} if key else {}
            raw=json.loads(fetch('https://api.openalex.org/works/https://doi.org/10.48550/arXiv.'+identity,headers,2_000_000));data=openalex_record(raw,identity)
        else:
            key=os.environ.get('SEMANTIC_SCHOLAR_API_KEY');headers={'x-api-key':key} if key else {}
            raw=json.loads(fetch('https://api.semanticscholar.org/graph/v1/paper/ARXIV:'+identity+'?fields=title,abstract,authors,externalIds,publicationDate',headers,2_000_000));data=semantic_record(raw,identity)
        with self.db() as db:
            save(db,data);db.execute("UPDATE paper_provider_attempts SET status='fetched' WHERE provider=? AND paper_id=? AND day=?",(provider,identity,today))
            db.execute("UPDATE paper_provider_state SET next_at=?,error='',checked_at=? WHERE provider=?",(time.time()+30,now(),provider))
    def oai(self,state):
        params={'verb':'ListRecords','resumptionToken':state['cursor']} if state['cursor'] else {'verb':'ListRecords','metadataPrefix':'arXivRaw','from':state['since']}
        records,deleted,cursor,day=parse_oai(fetch('https://oaipmh.arxiv.org/oai?'+urlencode(params)))
        with self.db() as db:
            for record in records:save(db,record)
            for identity in deleted:db.execute("UPDATE arxiv_papers SET status='withdrawn',error='OAI 레코드 삭제 표시' WHERE paper_id=?",(identity,))
            db.execute("UPDATE paper_provider_state SET next_at=?,cursor=?,since=?,error='',checked_at=? WHERE provider='arxiv_oai'",(time.time()+(30 if cursor else 86400),cursor,state['since'] if cursor else day or state['since'],now()))
