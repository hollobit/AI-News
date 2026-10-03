"""Keep only numeric provider usage from ephemeral CLI events, never event bodies."""
import json

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')


def read_usage(stream):
    turns=[]
    for line in stream:
        try:event=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(event,dict) or event.get('type')!='turn.completed':continue
        usage=event.get('usage')
        if not isinstance(usage,dict):continue
        valid={k:v for k,v in usage.items() if k in FIELDS and type(v) is int and v>=0}
        if not {'input_tokens','output_tokens'}<=valid.keys():continue
        if valid.get('cached_input_tokens',0)>valid['input_tokens']:continue
        if valid.get('reasoning_output_tokens',0)>valid['output_tokens']:continue
        turns.append(valid)
    if not turns:return {}
    return {key:sum(t[key] for t in turns) if all(key in t for t in turns) else None for key in FIELDS}


def persist(db, ticket):
    usage=getattr(ticket,'usage',{}) or {}
    if usage:
        db.execute('UPDATE llm_calls SET input_tokens=?,cached_input_tokens=?,output_tokens=?,reasoning_output_tokens=? WHERE id=?',
                   (*[usage.get(k) for k in FIELDS],ticket.id))


def totals(db, ids=None):
    where=''
    if ids is not None:
        if not ids:return {'measured_calls':0}
        where=' WHERE id IN ('+','.join('?' for _ in ids)+')'
    columns={r[1] for r in db.execute('PRAGMA table_info(llm_calls)')}
    if not set(FIELDS)<=columns:return {'measured_calls':0}
    sql='SELECT count(input_tokens),'+','.join('CASE WHEN count('+k+')=count(input_tokens) THEN sum('+k+') END' for k in FIELDS)+' FROM llm_calls'+where
    row=db.execute(sql,ids or []).fetchone()
    return dict(measured_calls=row[0],**dict(zip(FIELDS,row[1:])))


def failure_code(stream):
    from engine_errors import classify_failure
    code=None
    for line in stream:
        try:event=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(event,dict):continue
        message=None
        if event.get('type')=='error':message=event.get('message')
        elif event.get('type')=='turn.failed':
            error=event.get('error')
            if isinstance(error,dict):message=error.get('message')
        if isinstance(message,str):
            found=classify_failure(message)
            if found!='execution_failed':code=found
    return code
