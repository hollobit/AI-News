"""Lossless transport compaction; original evidence and persisted IDs stay intact."""
import copy
import json
import re

ALIAS = re.compile(r'(?<![A-Za-z0-9_])[EDK][1-9][0-9]*(?![A-Za-z0-9_])')
IDENTITY = re.compile(r'(?:news|url)_[0-9a-f]{20,64}')


def prepare(prompt, schema):
    # Restoring E1 to a long evidence ID can violate a bounded prose field.
    # Preserve original references for those contracts; never truncate output.
    def bounded_text(node):
        if isinstance(node,dict):
            return (node.get('type')=='string' and 'maxLength' in node and 'enum' not in node) or any(bounded_text(v) for v in node.values())
        return isinstance(node,list) and any(bounded_text(v) for v in node)
    if bounded_text(schema): return prompt,schema,{}
    head, separator, body = prompt.partition('\nDATA:\n')
    if not separator:return prompt,schema,{}
    try:data=json.loads(body)
    except (ValueError,TypeError):return prompt,schema,{}
    if isinstance(data,list) and head.startswith(('ROLE: baseline_analysis','ROLE: baseline_verification')):
        return prepare_documents(head,separator,data,schema,prompt)
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


def prepare_documents(head, separator, documents, schema, original):
    """Compress only structural document/evidence/keyword references, not sources."""
    if (not documents or any(not isinstance(d,dict) or not isinstance(d.get('document_id'),str)
                             or not isinstance(d.get('evidence'),list) for d in documents)
            or ALIAS.search(original) or ALIAS.search(json.dumps(schema))):
        return original,schema,{}
    document_ids=[d['document_id'] for d in documents]
    if len(set(document_ids))!=len(document_ids):return original,schema,{}
    evidence_ids=list(dict.fromkeys(e.get('id') for d in documents for e in d['evidence']))
    keywords=list(dict.fromkeys(k for d in documents for k in d.get('keyword_citations',{})))
    ids=document_ids+evidence_ids+keywords
    if any(not isinstance(i,str) or not i for i in ids) or len(ids)!=len(set(ids)):
        return original,schema,{}
    forward={identity:prefix+str(n+1) for prefix,entries in [('D',document_ids),('E',evidence_ids),('K',keywords)]
             for n,identity in enumerate(entries)}
    def encode(value,key=''):
        if isinstance(value,dict):return {forward.get(k,k) if key=='keyword_citations' else k:encode(v,k) for k,v in value.items()}
        if isinstance(value,list):return [encode(v,key) for v in value]
        if isinstance(value,str) and key in ('id','document_id','evidence_ids','checked_evidence_ids','citation_id','enum'):
            return forward.get(value,value)
        return value
    packed=head+separator+json.dumps(encode(documents),ensure_ascii=False,separators=(',',':'))
    return packed,encode(copy.deepcopy(schema)),{alias:identity for identity,alias in forward.items()}


def restore(value, aliases):
    if not aliases:return value
    if isinstance(value,dict):return {k:restore(v,aliases) for k,v in value.items()}
    if isinstance(value,list):return [restore(v,aliases) for v in value]
    if isinstance(value,str):return ALIAS.sub(lambda m:aliases.get(m[0],m[0]),value)
    return value
