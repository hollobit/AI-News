"""Project-local Agent-Reach bridge; no global installs, browser credentials or shell."""
from pathlib import Path
import json
import os
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parent
PYTHON=ROOT/'.runtime/agent-reach/venv/bin/python'
WORKER=ROOT/'integrations/agent-reach/worker.py'


def invoke(request, timeout=65):
    """Bound worker lifetime and output; never expose third-party tracebacks."""
    if not PYTHON.exists():return {'status':'unavailable','error':'Agent-Reach 실행 환경이 설치되지 않았습니다.','installed':False}
    with tempfile.TemporaryFile() as output:
        try:
            process=subprocess.run([str(PYTHON),str(WORKER)],input=json.dumps(request).encode(),
                stdout=output,stderr=subprocess.DEVNULL,timeout=timeout,cwd=ROOT,
                env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SSL_CERT_FILE','SYSTEMROOT'}})
            if process.returncode:raise ValueError('worker failed')
            if output.tell()>3*1024*1024:raise ValueError('output exceeded')
            output.seek(0);result=json.load(output)
            if not isinstance(result,dict):raise ValueError('invalid response')
            return result
        except (OSError,ValueError,subprocess.TimeoutExpired):
            return {'status':'failed','error':'읽기 도구의 실행 시간이 초과되었거나 응답을 처리하지 못했습니다.'}


def fetch_source(url):
    """Use Agent-Reach readers inside the existing source cache pipeline."""
    if not PYTHON.exists():
        from source_content import fetch_source as original
        return original(url)
    return invoke({'action':'read','url':url})


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','read'])
    parser.add_argument('url',nargs='?');parser.add_argument('--mode',choices=['auto','rss'],default='auto')
    args=parser.parse_args()
    print(json.dumps(invoke(vars(args)),ensure_ascii=False,indent=2))
