"""Bounded read-only HTTP/RSS sampling; never starts collection or model analysis."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen


def sample(base):
    result={'time':time.time(),'http':[],'processes':[]}
    for route in ('/api/news?page_size=40&compact=1','/api/graph/integrated?max_nodes=24&evidence_limit=12'):
        started=time.monotonic()
        try:
            with urlopen(base+route,timeout=30) as response:
                body=response.read();value=json.loads(body)
                result['http'].append(dict(path=route,status=response.status,seconds=round(time.monotonic()-started,3),bytes=len(body),state=value.get('status','ready')))
        except Exception as error:result['http'].append(dict(path=route,error=type(error).__name__,seconds=round(time.monotonic()-started,3)))
    process=subprocess.run(['ps','-axo','pid=,ppid=,rss=,args='],capture_output=True,text=True,check=True)
    for line in process.stdout.splitlines():
        parts=line.strip().split(None,3)
        if len(parts)<4:continue
        command=parts[3].split()
        if len(command)<2 or not Path(command[0]).name.startswith('python'):continue
        script=Path(command[1])
        if script.is_absolute() and script.parent!=Path(__file__).resolve().parent:continue
        if script.name not in {'app.py','projection_worker.py','graph_snapshot.py','sync_wiki_pages.py'}:continue
        result['processes'].append(dict(pid=int(parts[0]),parent=int(parts[1]),rss_kib=int(parts[2]),script=script.name))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8001')
    parser.add_argument('--samples',type=int,default=6);parser.add_argument('--interval',type=float,default=10)
    args=parser.parse_args()
    if not 1<=args.samples<=120 or not 0<=args.interval<=60:raise SystemExit('Invalid bounded probe interval')
    for i in range(args.samples):
        print(json.dumps(sample(args.base)),flush=True)
        if i+1<args.samples:time.sleep(args.interval)
