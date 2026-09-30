"""Cached, content-free preflight for the exact requested news model set."""
import argparse
import fcntl
import json
from pathlib import Path
import time
from model_policy import policy, VERSION

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.runtime/news-model-access.json'


def ensure_model_access(*, force=False, state_path=STATE, caller=None, stamp=None):
    stamp=time.time() if stamp is None else stamp
    selected=[('engine_probe',False),('baseline_verification',False),('risk_verification',True)]
    expected=[policy(role,escalation=escalation)['model'] for role,escalation in selected]
    state_path=Path(state_path);state_path.parent.mkdir(parents=True,exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'ready':False,'stage':'checking_models','models':expected}
        try:prior=json.loads(state_path.read_text())
        except (OSError,ValueError):prior={}
        ttl=21600 if prior.get('ready') else 1800
        if not force and prior.get('models')==expected and prior.get('policy_version')==VERSION and 0<=stamp-prior.get('checked_at',0)<ttl:return prior
        if caller is None:
            from app import load_local_env
            from semantic import run_structured
            load_local_env();caller=run_structured
        checks=[]
        schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
        from engine_errors import infrastructure_error
        for (role,escalation),model in zip(selected,expected):
            try:
                result=caller('Return {"ok":true}. No tools or external facts.',schema,role=role,escalation=escalation,timeout=30,queue_timeout=10)
                checks.append({'model':model,'ok':result=={'ok':True},'error_code':'' if result=={'ok':True} else 'invalid_output'})
            except Exception as exc:checks.append({'model':model,'ok':False,'error_code':infrastructure_error(str(exc)) or 'request_failed'})
        result={'ready':all(c['ok'] for c in checks),'models':expected,'checks':checks,'checked_at':stamp,'policy_version':VERSION}
        temporary=state_path.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2));temporary.replace(state_path)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    print(json.dumps(ensure_model_access(force=args.refresh),ensure_ascii=False,indent=2))
