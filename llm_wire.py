"""Lossless transport compaction; original evidence and persisted IDs stay intact."""
import copy
import json
import re

ALIAS = re.compile(r'(?<![A-Za-z0-9_])E[1-9][0-9]*(?![A-Za-z0-9_])')
IDENTITY = re.compile(r'(?:news|url)_[0-9a-f]{20,64}')


def prepare(prompt, schema):
    head, separator, body = prompt.partition('\nDATA:\n')
    if not separator:return prompt,schema,{}
    try:data=json.loads(body)
    except (ValueError,TypeError):return prompt,schema,{}
    if not isinstance(data,dict):return prompt,schema,{}
    entries=data.get('evidence')
    if not isinstance(entries,list):return prompt,schema,{}
    ids=[e.get('id') for e in entries if isinstance(e,dict)]
    if not ids or any(not isinstance(i,str) or not IDENTITY.fullmatch(i) for i in ids) or len(ids)!=len(set(ids)):return prompt,schema,{}
    # Existing E1-style facts must never be confused with transport identifiers.
    if ALIAS.search(prompt) or ALIAS.search(json.dumps(schema)):return prompt,schema,{}
    if any(identity in str(e.get('text','')) for e in entries for identity in ids):return prompt,schema,{}
    forward={identity:'E'+str(i+1) for i,identity in enumerate(ids)}
    references=re.compile(r'(?<![A-Za-z0-9_])(?:'+'|'.join(re.escape(identity) for identity in ids)+r')(?![A-Za-z0-9_])')
    def encode(value,key='',source=False):
        if isinstance(value,dict):return {k:encode(v,k,source) for k,v in value.items()}
        if isinstance(value,list):return [encode(v,key,source or key=='evidence') for v in value]
        if isinstance(value,str) and (key=='id' or key.endswith('_id') or key.endswith('_ids') or key=='enum'):
            return forward.get(value,value)
        if isinstance(value,str) and not source:
            return references.sub(lambda m:forward[m[0]],value)
        return value
    # Text/date/source fields are not truncated or rewritten. Only reference
    # values and enum alternatives change; JSON whitespace is insignificant.
    packed=head+separator+json.dumps(encode(data),ensure_ascii=False,separators=(',',':'))
    return packed,encode(copy.deepcopy(schema)),{alias:identity for identity,alias in forward.items()}


def restore(value, aliases):
    if not aliases:return value
    if isinstance(value,dict):return {k:restore(v,aliases) for k,v in value.items()}
    if isinstance(value,list):return [restore(v,aliases) for v in value]
    if isinstance(value,str):return ALIAS.sub(lambda m:aliases.get(m[0],m[0]),value)
    return value
