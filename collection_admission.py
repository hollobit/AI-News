"""A disconnected collector must not hold already committed extraction work."""
from datetime import datetime
import json


def admission(states, stamp):
    def timestamp(value):
        try:
            parsed=datetime.fromisoformat(value)
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except (TypeError,ValueError):
            return None
    heartbeat=timestamp(states.get('collector_last_success'))
    try:extraction=json.loads(states.get('collector_corpus_snapshot','{}'))
    except (ValueError,TypeError):extraction={}
    if not isinstance(extraction,dict):extraction={}
    fresh=heartbeat is not None and 0<=stamp-heartbeat<=120
    if extraction.get('status')!='complete':
        return {'stage':'waiting_for_extraction' if fresh else 'waiting_for_collector'}
    if fresh:return {'input_mode':'current_collection','collector_current':True}
    # Old schema/incomplete commits never qualify. The collector persists the
    # extracting marker with changed articles and only publishes these fields
    # after deduplication/indexing completes. Do not invent a fresh heartbeat.
    checked=timestamp(extraction.get('checked_at'))
    extracted=timestamp(extraction.get('extracted_at'))
    total=extraction.get('total_unique')
    if (heartbeat is None or checked is None or extracted is None
            or not checked<=heartbeat<=stamp or not checked<=extracted<=stamp
            or type(total) is not int or total<0):
        return {'stage':'waiting_for_collector'}
    return {'input_mode':'stored_extraction','collector_current':False,
            'input_extracted_at':extraction['extracted_at']}
