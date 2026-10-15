"""Run labeled, event-separated holdout decisions without changing live routing.

Each JSONL row: id, group, split='holdout', purpose, state, questions, expected.
expected maps question IDs to manually reviewed choice labels. Use the same rows
with pinned model versions; no model output is its own quality label.
"""
import argparse
import json
from pathlib import Path
from statistics import median
import time
from system_one import evaluate, status


def run(rows,path,caller=None):
    groups=set();ids=set()
    for row in rows:
        if row.get('split')!='holdout' or not row.get('group') or row['group'] in groups:
            raise ValueError('서로 다른 사건 그룹의 holdout 사례가 필요합니다.')
        if not row.get('id') or row['id'] in ids:raise ValueError('평가 ID가 중복되었습니다.')
        groups.add(row['group']);ids.add(row['id'])
        if not row.get('expected') or set(row['expected'])!=set(row['questions']):raise ValueError('모든 질의에 독립 정답 라벨이 필요합니다.')
        for key,label in row['expected'].items():
            question=row['questions'][key]
            if question['type']!='choice' or label not in question['criteria']:raise ValueError('유효한 Choice 정답이 필요합니다.')
    results=[];correct=labeled=abstained=unsafe=0
    for row in rows:
        started=time.monotonic()
        result=evaluate(path,row['purpose'],row['state'],row['questions'],row['expected'],caller=caller)
        item=dict(id=row['id'],status=result['status'],cached=result.get('cached',False),elapsed_ms=round((time.monotonic()-started)*1000,2),usage=result.get('usage',{}),checks={})
        if result['status']=='complete':
            for key,label in row['expected'].items():
                answer=result['answers'][key];actual=answer['choice']
                item['checks'][key]=dict(expected=label,actual=actual,confidence=answer['confidence'])
                labeled+=1;correct+=actual==label;abstained+=actual in ('unknown','unclear')
                unsafe+=label in ('required','present','full','counter') and actual in ('routine','absent','compact','unrelated')
        results.append(item)
    measured=[r['elapsed_ms'] for r in results if r['status']=='complete' and not r['cached']]
    return dict(settings=status(path),cases=len(rows),labeled=labeled,correct=correct,abstentions=abstained,
                unsafe_choices=unsafe,accuracy=correct/labeled if labeled else None,
                uncached_p50_ms=median(measured) if measured else None,results=results,
                activation_allowed=False,note='분류 비교만 측정합니다. 답변 품질·인용·위험 회수율 및 전체 처리시간 평가는 별도로 필요합니다.')


if __name__=='__main__':
    from app import load_local_env
    load_local_env()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--database',default='data/news.sqlite3')
    args=parser.parse_args()
    if args.input:
        if not args.output:parser.error('--input에는 --output이 필요합니다.')
        rows=[json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
        result=run(rows,args.database)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False))
    else:print(json.dumps(status(args.database),ensure_ascii=False,indent=2))
