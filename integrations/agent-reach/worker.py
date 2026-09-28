"""Isolated Agent-Reach adapters. Read public sources; emit bounded JSON only."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from source_content import fetch_source, _validated_target, _result


def status():
    """Describe installed registry and implemented readers without probing accounts."""
    from importlib.metadata import version
    from agent_reach.channels import get_all_channels
    supported = {'web':'일반 웹·Jina Reader', 'twitter':'공개 단일 게시물',
                 'github':'공개 저장소 README', 'youtube':'공개 영상 설명·제공 자막', 'rss':'RSS/Atom 항목'}
    return {'installed':True, 'version':version('agent-reach'), 'channels':[
        {'id':c.name, 'name':c.description, 'backends':c.backends,
         'status':'available' if c.name in supported else 'configuration_required',
         'scope':supported.get(c.name,'추가 도구·로그인 설정 필요; 현재 자동 수집 대상 아님')}
        for c in get_all_channels()], 'availability':'설치·연결 범위입니다. 개별 사이트 접근 성공은 실제 읽기 결과로 확인합니다.'}


def document(url, text, title, reader, platform, scope, **extra):
    result = _result(url,status='fetched' if text.strip() else 'failed',text=text[:250000],
                     title=title[:500],content_type='text/plain',truncated=len(text)>250000,
                     error='' if text.strip() else '가져올 수 있는 공개 본문이 없습니다.')
    return dict(result,reader=reader,platform=platform,evidence_scope=scope,**extra)


def normalized_date(value):
    from datetime import datetime,timezone
    from email.utils import parsedate_to_datetime
    try:
        try:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:dt=parsedate_to_datetime(value)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,AttributeError):return ''


def search(query):
    """Use the Exa MCP backend registered by Agent-Reach, without global CLI configuration."""
    if not isinstance(query,str) or not 3<=len(query)<=500:raise ValueError('검색어 길이 오류')
    import requests
    endpoint='https://mcp.exa.ai/mcp'
    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    def rpc(payload):
        with requests.post(endpoint,json=payload,headers=headers,timeout=(5,20),stream=True,allow_redirects=False) as response:
            response.raise_for_status()
            session=response.headers.get('mcp-session-id')
            if session:headers['Mcp-Session-Id']=session
            body=b''
            for block in response.iter_content(8192):
                body+=block
                if len(body)>2_000_000:raise ValueError('검색 응답 크기 초과')
                if b'\n\n' in body and b'data:' in body:break
            text=body.decode('utf-8')
            if 'text/event-stream' in response.headers.get('content-type',''):
                text=next(line[5:].strip() for line in text.splitlines() if line.startswith('data:'))
            value=json.loads(text)
            if value.get('error'):raise ValueError('검색 서비스 오류')
            return value.get('result',{})
    rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'news-agent-reach','version':'1'}}})
    value=rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'web_search_exa','arguments':{'query':query,'numResults':5}}})
    if value.get('isError'):return {'status':'failed','error':'외부 검색 제공자가 요청을 처리하지 못했습니다.','entries':[]}
    body='\n'.join(c.get('text','') for c in value.get('content',[]) if c.get('type')=='text')
    # Search snippets are discovery clues only; each URL must be fetched separately.
    urls=re.findall(r'(?m)^URL:\s*(https?://\S+)',body)
    if not urls:urls=re.findall(r'https?://[^\s<>\]\)"\x00-\x1f]+',body)
    return {'status':'fetched','reader':'agent-reach-exa-mcp','query':query,'entries':[{'url':u.rstrip('.,;'),'title':''} for u in dict.fromkeys(urls)][:5]}


def twitter(url):
    match = re.fullmatch(r'/(?:[A-Za-z0-9_]+/status|i/web/status)/(\d+)(?:/.*)?',urlsplit(url).path)
    if not match:return _result(url,status='unsupported',error='X는 공개 단일 게시물 주소만 지원합니다. 검색·타임라인은 로그인 설정이 필요합니다.')
    result=fetch_source('https://api.fxtwitter.com/status/'+match[1],raw=True)
    if result['status']!='fetched':return _result(url,status='failed',error='공개 X 게시물 조회에 실패했습니다.')
    data=json.loads(result['body']);tweet=data.get('tweet') or {}
    if data.get('code')!=200 or str(tweet.get('id'))!=match[1]:return _result(url,status='failed',error='공개 게시물을 찾지 못했습니다.')
    return document(url,tweet.get('text') or '',(tweet.get('author') or {}).get('name','X 게시물'),
                    'fxtwitter-public','twitter','post_text; video_not_transcribed',
                    published_at=tweet.get('created_at',''),media_count=len((tweet.get('media') or {}).get('all',[])))


def github(url):
    parts=urlsplit(url).path.strip('/').split('/')
    if len(parts)!=2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+',x) and x not in {'.','..'} for x in parts):
        return web(url)
    owner,repo=parts;repo=repo.removesuffix('.git')
    result=fetch_source(f'https://api.github.com/repos/{owner}/{repo}/readme',raw=True)
    if result['status']!='fetched':return web(url)
    import base64
    data=json.loads(result['body'])
    text=base64.b64decode(data.get('content','')).decode('utf-8',errors='replace')
    return document(url,text,f'{owner}/{repo} README','github-public-api','github','repository_readme')


def rss(url):
    import feedparser
    result=fetch_source(url,raw=True)
    if result['status']!='fetched':return result
    feed=feedparser.parse(result['body'])
    if not feed.version:return _result(url,status='unsupported',error='RSS/Atom 문서가 아닙니다.')
    from source_content import _extract
    entries=[]
    for entry in feed.entries[:20]:
        summary=_extract(('<body>'+entry.get('summary','')+'</body>').encode(),'text/html','text/html')[1]
        entries.append({'title':entry.get('title',''),'url':entry.get('link',''),'summary':summary[:2000],
                        'published_at':normalized_date(entry.get('published') or entry.get('updated') or '')})
    text='\n\n'.join(e['title']+'\n'+e['summary']+'\n'+e['url'] for e in entries)
    return document(url,text,feed.feed.get('title','RSS/Atom'),'feedparser','rss','feed_summaries_not_full_articles',entries=entries)


def youtube(url):
    from yt_dlp import YoutubeDL
    p=urlsplit(url);identity=p.path.strip('/') if p.hostname=='youtu.be' else parse_qs(p.query).get('v',[''])[0]
    if not identity and p.path.startswith(('/shorts/','/embed/')):identity=p.path.split('/')[2]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',identity):return _result(url,status='unsupported',error='YouTube 단일 영상 주소가 필요합니다.')
    class Quiet:
        def debug(self,*a):pass
        def warning(self,*a):pass
        def error(self,*a):pass
    options={'quiet':True,'no_warnings':True,'logger':Quiet(),'skip_download':True,'noplaylist':True,
             'cachedir':False,'socket_timeout':10,'retries':0,'extractor_retries':0,'js_runtimes':{'node':{}},
             'remote_components':set()}
    with YoutubeDL(options) as ydl:info=ydl.extract_info('https://www.youtube.com/watch?v='+identity,download=False)
    text=info.get('description') or '';scope='video_description_only'
    for field in ['subtitles','automatic_captions']:
        tracks=info.get(field) or {}
        language=next((l for l in ['ko','en','en-orig'] if l in tracks),None)
        if not language:continue
        track=next((t for t in tracks[language] if t.get('ext')=='vtt'),None)
        if not track:continue
        subtitle=fetch_source(track['url'],raw=True)
        if subtitle['status']=='fetched':
            body=subtitle['body'].decode('utf-8',errors='replace')
            lines=[re.sub('<[^>]+>','',line) for line in body.splitlines() if line.strip() and not line.startswith(('WEBVTT','Kind:','Language:'))]
            text+='\n\n자막 ('+language+')\n'+'\n'.join(lines)
            scope='automatic_captions' if field=='automatic_captions' else 'provided_captions'
            break
    return document(url,text,info.get('title','YouTube'),'yt-dlp','youtube',scope,published_at=info.get('upload_date',''))


def web(url, prefer_reader=False):
    from source_enrichment import source_quality_error
    result=fetch_source(url)
    if not prefer_reader and result['status']=='fetched' and result.get('text','').strip() and not source_quality_error(result):
        return dict(result,reader='direct-public',platform='web',evidence_scope='web_excerpt')
    if result['status']=='blocked':return result
    # Validate original address before forwarding it to an external public reader.
    _validated_target(url)
    fallback=fetch_source('https://r.jina.ai/'+url)
    if fallback['status']=='fetched':
        text=fallback.get('text','');marker='Markdown Content:'
        if marker not in text:return _result(url,status='failed',error='Reader에서 본문을 반환하지 않았습니다.')
        title=text.splitlines()[0].removeprefix('Title: ').strip()
        text=text.split(marker,1)[1].strip()
        if re.search(r'(?i)captcha|access denied|sign in to continue|just a moment|checking your browser',text[:500]) and len(text)<1500:
            return _result(url,status='failed',error='접근 확인 페이지가 반환되어 근거로 저장하지 않았습니다.')
        return document(url,text,title,'jina-reader','web','web_excerpt')
    if result.get('status')=='fetched' and not source_quality_error(result):return dict(result,reader='direct-public',platform='web',evidence_scope='web_excerpt')
    return dict(result,reader='direct-public+jina-reader',fallback_error=fallback.get('error',''))


def read(url, mode='auto'):
    _validated_target(url)
    from agent_reach.channels import get_all_channels
    if mode=='rss':return rss(url)
    if mode=='reader':return web(url,prefer_reader=True)
    channel=next((c for c in get_all_channels() if c.name!='web' and c.can_handle(url)),None)
    platform=channel.name if channel else 'web'
    if platform in {'twitter','github','youtube'}:return globals()[platform](url)
    if platform not in {'web','rss','linkedin'}:
        return _result(url,status='unsupported',error='이 플랫폼은 추가 도구·로그인 설정이 필요합니다.')
    if platform=='rss' or urlsplit(url).path.lower().endswith(('.rss','.atom','.xml')):return rss(url)
    return web(url)


if __name__=='__main__':
    try:
        request=json.load(sys.stdin)
        result=status() if request['action']=='status' else search(request['query']) if request['action']=='search' else read(request['url'],request.get('mode','auto'))
    except PermissionError:
        result={'status':'blocked','error':'비공개·내부 네트워크 주소는 읽을 수 없습니다.'}
    except Exception:
        result={'status':'failed','error':'원문을 읽지 못했습니다. 주소 또는 플랫폼 접근 상태를 확인해 주세요.'}
    print(json.dumps(result,ensure_ascii=False))
