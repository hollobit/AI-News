"""Short wire references, with exact restoration before citation validation."""
from copy import deepcopy
import json
import re


def pack(data, schema):
    ids=[e['id'] for e in data['evidence']]
    if len(ids)!=len(set(ids)):raise ValueError('중복된 인용 식별자')
    raw=json.dumps([data,schema],ensure_ascii=False)
    prefix='CITE'
    while prefix in raw:prefix+='X'
    forward={identity:prefix+str(i+1) for i,identity in enumerate(ids)}
    def encode(value,field=''):
        if isinstance(value,dict):return {k:encode(v,k) for k,v in value.items()}
        if isinstance(value,list):return [encode(v,field) for v in value]
        if isinstance(value,str) and field in ('id','evidence_ids','enum'):
            return forward.get(value,value)
        return value
    return encode(data),encode(deepcopy(schema)),{alias:identity for identity,alias in forward.items()}


def restore(value, aliases):
    if not aliases:return value
    pattern=re.compile(r'(?<![A-Za-z0-9_])(?:'+'|'.join(map(re.escape,sorted(aliases,key=len,reverse=True)))+r')(?![A-Za-z0-9_])')
    def decode(item):
        if isinstance(item,dict):return {k:decode(v) for k,v in item.items()}
        if isinstance(item,list):return [decode(v) for v in item]
        if isinstance(item,str):return pattern.sub(lambda m:aliases[m[0]],item)
        return item
    return decode(value)
