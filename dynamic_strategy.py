"""Connect discovered topics, their management registry, and strategic news views."""
from copy import deepcopy
from projection_cache import cached_read, revision_token, content_digest


_UNSET = object()


def dynamic_projection(db, items, morph, source_revision=_UNSET):
    from dynamic_registry import list_registry
    from dynamic_topics import build_dynamic_topics
    registry = list_registry(db)
    source = revision_token(db, ('source',)) if source_revision is _UNSET else source_revision
    # Automatic counts are output of this projection, not an input rule change.
    # Keying on their history version repeatedly invalidates our own result.
    rules=[(entry['id'],entry.get('version')) for entry in registry['items'] if entry.get('origin')=='manual']
    exclusions=sorted(entry['id'] for entry in registry['items'] if entry.get('excluded'))
    key = None if source is None else (source, content_digest([sorted(rules),exclusions]))
    def build():
        from dynamic_cooccurrence import build_cooccurrence_candidates
        result = build_dynamic_topics(db, items, morph)
        result['candidates'] = build_cooccurrence_candidates(db, items, morph)
        return result
    return cached_read(db, 'dynamic-strategy-v3-rules', key, build)


def apply_dynamic_trends(db, items, morph, trends, source_revision=_UNSET):
    from dynamic_registry import list_registry
    discovered = dynamic_projection(db, items, morph, source_revision)
    registry = list_registry(db)
    excluded = {entry['id'] for entry in registry['items'] if entry.get('excluded')}
    result = deepcopy(trends)
    topics = [dict(entry, verification_label='원문 표현·문서 수 규칙 확인')
              for entry in discovered.get('topics', []) if entry['id'] not in excluded]
    signals = [dict(entry, verification_label='수집 문장의 AI·통제·변화 문맥 규칙 확인')
               for entry in discovered.get('signals', []) if entry['id'] not in excluded]
    result['lenses'] = [entry for entry in result['lenses'] if entry['id'] not in excluded] + topics
    # Signals are independently discoverable filters as well as monitoring cards.
    result['dynamic_signal_lenses'] = [{k:v for k,v in s.items() if k!='_observations'} for s in signals]
    result['monitoring']['topics'] = [entry for entry in result['monitoring']['topics'] if entry['id'] not in excluded] + signals
    previous_candidates = [dict(term,label=topic.get('label',topic.get('name',''))+' · '+term['label'])
                           for topic in result['monitoring']['topics'] if not topic.get('dynamic')
                           for term in topic.get('related_keywords',[])]
    result['monitoring']['candidates'] = [dict(entry,dynamic=True,verification_label='같은 원문 내 두 표현·고유 문서 수 확인')
                                         for entry in discovered.get('candidates',[]) if entry['id'] not in excluded] + previous_candidates
    from monitoring_groups import merge_signals
    result['monitoring']['topics']=merge_signals(result['monitoring']['topics'],result['start'],result['end'])
    result['monitoring']['method']+=' 고정 주제와 자동 발견 신호를 유사한 통제 대상·행동별로 통합하며 같은 문서는 묶음 안에서 한 번만 집계합니다. 유형별 건수에는 같은 문서가 포함될 수 있습니다. 추이는 현재 분류 기준으로 날짜별 자료를 재집계한 결과입니다. 같은 문서가 여러 날짜에 등장하면 일별 합계와 기간 고유 문서 수는 다를 수 있습니다.'
    result['dynamic_topics'] = topics
    result['discovery'] = {key:value for key,value in discovered.items() if key not in {'topics','signals','candidates'}}
    result['registry_version'] = registry['version']
    result['version'] = content_digest(result)
    return result


def filter_dynamic_lens(db, items, lens_id, records=None,registry=None):
    from dynamic_registry import list_registry
    from dynamic_topics import match_dynamic_item
    from morphology import keyword_records
    from keyword_index import keyword_record_id
    entries = list_registry(db)['items'] if registry is None else registry
    entry = next((entry for entry in entries if entry['id']==lens_id), None)
    if entry is None or entry.get('excluded'):
        raise ValueError('제외되었거나 존재하지 않는 전략 주제입니다.')
    if records is None:
        records = keyword_records(db, items)
    return [item for item in items if match_dynamic_item(item,entry,records.get(keyword_record_id(item), []))]


def discovery_followups(db, limit=8):
    """Offer observed terms to the existing RSI queue without turning them into facts."""
    from dynamic_registry import list_registry
    entries=list_registry(db,include_excluded=False)['items']
    candidates=[]
    for entry in entries:
        metadata=entry.get('metadata') or {}
        if entry['origin']=='builtin' or entry['status']!='active' or not metadata.get('current') or not metadata.get('evidence'):
            continue
        candidates.append(entry)
    candidates.sort(key=lambda entry:(entry['kind']!='signal',-(entry['metadata'].get('strategic_score') or 0),entry['id']))
    return [{'kind':'review_discovered_topic','source_topic_id':entry['id'],
             'search_terms':entry['terms'][:6],
             'text':'자동 관측 후보의 의미·변화 여부·반대 근거 검토: '+entry['label'],
             'status':'open','epistemic_status':'rule_checked_observation'} for entry in candidates[:limit]]
