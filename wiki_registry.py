"""User-controlled wiki scope; settings never constitute reviewed evidence."""
import json
import re
from bulk_baseline import digest
from wiki_sources import TOPICS, exists


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS wiki_settings(
        id TEXT PRIMARY KEY, config_json TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS wiki_aliases(
        source TEXT PRIMARY KEY,target TEXT NOT NULL,reason TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS wiki_setting_events(
        id INTEGER PRIMARY KEY,kind TEXT NOT NULL,detail_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def topics(db):
    result = {key: dict(value, enabled=True, historical=False) for key, value in TOPICS.items()}
    if exists(db, 'wiki_settings'):
        for row in db.execute('SELECT id,config_json FROM wiki_settings'):
            result[row[0]] = json.loads(row[1])
    return result


def configure(db, payload):
    identity = payload.get('topic')
    if not isinstance(identity, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', identity):
        raise ValueError('주제 ID는 영문 소문자·숫자·밑줄·하이픈 1~64자입니다.')
    old = topics(db).get(identity)
    name = payload.get('name', (old or {}).get('name'))
    terms = payload.get('terms', (old or {}).get('terms'))
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('주제 이름은 1~100자입니다.')
    if not isinstance(terms, list) or not 1 <= len(terms) <= 30 or any(not isinstance(t, str) or not 2 <= len(t.strip()) <= 80 for t in terms):
        raise ValueError('검색어는 2~80자, 1~30개입니다.')
    enabled = payload.get('enabled', (old or {}).get('enabled', True))
    historical = payload.get('historical', (old or {}).get('historical', False))
    if type(enabled) is not bool or type(historical) is not bool:
        raise ValueError('활성화와 과거 근거 설정은 참/거짓이어야 합니다.')
    config = dict(id=identity, name=name.strip(), terms=list(dict.fromkeys(t.strip() for t in terms)), enabled=enabled, historical=historical)
    db.execute('INSERT INTO wiki_setting_events(kind,detail_json) VALUES (?,?)',
               ('topic', json.dumps(dict(before=old, after=config), ensure_ascii=False)))
    db.execute('INSERT OR REPLACE INTO wiki_settings VALUES (?,?)', (identity, json.dumps(config, ensure_ascii=False)))
    db.execute('INSERT OR IGNORE INTO wiki_topics(id) VALUES (?)', (identity,))
    db.execute('UPDATE wiki_topics SET requested=1 WHERE id=?', (identity,))
    return config


def config_token(config):
    return digest({k: config[k] for k in ('name', 'terms', 'historical')})


def matches_config(db, bundle):
    config = topics(db).get(bundle['topic'])
    if not config or not config['enabled']:
        return False
    token = bundle.get('config_hash')
    return config_token(config) == token if token else config == dict(TOPICS.get(bundle['topic'], {}), enabled=True, historical=False)


def export_markdown(view):
    """Plain escaped Markdown; only the current verified view is exportable."""
    def escape(value):
        import html
        value = html.escape(str(value), quote=False)
        return re.sub(r'([\\`*_{}\[\]()#+!|>])', r'\\\1', value)
    lines = ['# 뉴스 지식 위키', '', '현재 입력과 독립 검토가 일치하는 페이지입니다. 사실성 보증은 아닙니다.', '']
    for page in view['pages']:
        lines += ['## ' + escape(page['title']), '', '판 ' + str(page['revision']), '', escape(page['scope']), '']
        for claim in page['claims']:
            lines += ['- ' + escape(claim['text']) + ' — ' + ', '.join(escape(r) for r in claim['evidence_ids'])]
        lines += ['', '### 원근거', '']
        for source in page['evidence']:
            lines += ['- ' + escape(source['id']) + ': ' + escape(source['title']), '  ' + escape(source.get('source_url', ''))]
        lines += ['']
    for link in view['links']:
        lines += ['- ' + escape(link['source_title']) + ' → ' + escape(link['target_title']) + ': ' + escape(link['text'])]
    return '\n'.join(lines) + '\n'
