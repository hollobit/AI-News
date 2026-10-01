import json
import sqlite3
from urllib.parse import urlsplit
import pytest
from url_parser import links
from classification import _safe_urls
from link_groups import canonical_url,build_link_groups
from url_archive import extract_message_urls,archive_message
from source_enrichment import SourceService

URLS=[
 'https://example.org/report_(final).pdf?filter[a]=1&filter[b]=2#p3',
 'https://example.org/a?next=https://other.org/p?a=1&b=2&flag&empty=',
 'https://example.org/a?ids[]=2&ids[]=1&data={a:b}',
 'https://example.org/a?x=a%2fb%20c&x=a+b&x=%252F',
 'https://example.org/a/?X-Amz-Signature=Ab%2BC&X-Amz-Date=20261001&utm_source=required',
]

@pytest.mark.parametrize('url',URLS)
def test_all_extractors_preserve_complete_url(url):
 text=f'[원출처]({url})'
 assert links(text)==[url]
 assert _safe_urls(text)==[url]
 assert [r['original_url'] for r in extract_message_urls({'text':text})]==[url]
 assert urlsplit(canonical_url(url)).query==urlsplit(url).query


def test_explicit_telegram_entity_preserves_trailing_punctuation():
 url='https://example.org/search?q=why?'
 assert extract_message_urls({'text':'출처','entities':[{'type':'text_link','offset':0,'length':2,'url':url}]})[0]['original_url']==url
 assert canonical_url(url)==url


def test_query_order_and_signature_are_not_rewritten():
 assert canonical_url(URLS[-1])==URLS[-1]
 assert canonical_url('https://example.org/a?x=2&x=1')!=canonical_url('https://example.org/a?x=1&x=2')


def test_fetch_uses_observed_original_not_grouping_key(tmp_path):
 from app import connect
 url='https://example.org/report/?a=x%20y&utm_source=feed#part'
 with connect(tmp_path/'db') as db:
  archive_message(db,{'chat':{'id':1,'title':'test'},'message_id':1,'date':1,'text':url})
 seen=[]
 service=SourceService(tmp_path/'db',fetcher=lambda u:(seen.append(u) or {'status':'fetched','title':'Report','text':'Actual body'}))
 try:
  service.fetch(canonical_url(url))
  assert seen==[url]
 finally:service.close()


def test_repair_changes_only_proven_truncation_and_is_idempotent(tmp_path):
 from app import connect
 from url_integrity import repair
 url=URLS[0];old=url.split(')')[0]
 with connect(tmp_path/'db') as db:
  db.execute('INSERT INTO articles VALUES(?,?,?,?,?,?,?,?,?,?,?)',('1',1,0,'title','summary','[Report]('+url+')','2026-10-01','article','general',old,'article'))
  assert repair(db)['article_urls']==1
  assert db.execute('select source_url from articles').fetchone()[0]==old
  result=repair(db,apply=True);assert result['article_urls']==1
  assert db.execute('select source_url from articles').fetchone()[0]==url
  assert json.loads(db.execute("select prior_json from url_integrity_history where kind='article'").fetchone()[0])['source_url']==old
  assert repair(db,apply=True)['article_urls']==0


def test_navigation_repairs_old_links_without_rewriting_historical_reports(tmp_path):
 from app import connect
 from url_integrity import repair
 from source_navigation import original_url
 url=URLS[0];old=url.split(')')[0]
 with connect(tmp_path/'db') as db:
  db.execute('INSERT INTO articles VALUES(?,?,?,?,?,?,?,?,?,?,?)',('1',1,0,'title','summary',url,'2026-10-01','article','general',old,'article'))
  repair(db,apply=True)
  assert original_url(db,old)==url
  db.execute('INSERT INTO repaired_source_links VALUES(?,?)',(old,url+'&other=1'))
  assert original_url(db,old)==old  # Ambiguous corrections never guess.


def test_source_redirect_preserves_query_bytes_and_rejects_header_injection(tmp_path):
 from types import SimpleNamespace
 from server_routes.articles import get
 class Handler:
  services=SimpleNamespace(path=str(tmp_path/'db'))
  def __init__(self):self.headers={};self.status=None
  def send_response(self,status):self.status=status
  def send_header(self,k,v):self.headers[k]=v
  def end_headers(self):pass
  def send_json(self,result,status):self.status=status
 for url in URLS:
  h=Handler();assert get(h,SimpleNamespace(path='/source-link'),{'url':[url]})
  assert h.status==302 and h.headers['Location']==url.replace('{','%7B').replace('}','%7D')
 for url in ['javascript:alert(1)','https://example.org/\r\nInjected: x','https://user:secret@example.org/']:
  h=Handler();get(h,SimpleNamespace(path='/source-link'),{'url':[url]});assert h.status==400
