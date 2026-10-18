"""Reuse optional source-bound editing notes without another model prepass."""
import json
from pathlib import Path
from bulk_baseline import digest


def purpose():
    return (Path(__file__).resolve().parent / 'WIKI_PURPOSE.md').read_text()


def editing_notes(db, bundle):
    """An absent/invalid optional hint never blocks full-source compilation.

    Legacy notes remain useful only for this exact input and purpose, with
    quotes still checked against the originals. They are never evidence.
    """
    cached = db.execute('SELECT result_json FROM wiki_structures WHERE input_hash=?', (key(bundle),)).fetchone()
    if cached:
        try:
            result = json.loads(cached[0])
            validate(result, bundle)
            return result
        except (ValueError, TypeError, KeyError):
            pass
    return {'notes': []}


def validate(result,bundle):
    sources = {e['id']:e for e in bundle['evidence']}
    if not isinstance(result,dict) or set(result)!={'notes'} or not isinstance(result['notes'],list) or len(result['notes'])>16:
        raise ValueError('위키 구조화 형식 오류')
    for note in result['notes']:
        if (not isinstance(note,dict) or set(note)!={'type','label','evidence_id','quote'}
            or note['type'] not in ('entity','concept','event','claim','question')
            or not isinstance(note['label'],str) or not 1<=len(note['label'].strip())<=150
            or not isinstance(note['evidence_id'],str) or note['evidence_id'] not in sources
            or not isinstance(note['quote'],str) or not 1<=len(note['quote'].strip())<=400
            or note['quote'] not in sources[note['evidence_id']]['text']):
            raise ValueError('위키 구조화 원문 인용 오류')


def key(bundle):
    return digest(['structure-1',bundle['input_hash'],purpose()])
