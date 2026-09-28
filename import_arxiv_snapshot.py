"""Download official Kaggle metadata; retain only already discovered paper IDs."""
import argparse,json,sqlite3,zipfile,shutil
from pathlib import Path
from urllib.request import Request,urlopen
from paper_metadata_sources import init,save,snapshot_record,now
ROOT=Path(__file__).resolve().parent

def run(path,archive):
    archive=Path(archive);archive.parent.mkdir(parents=True,exist_ok=True)
    if not archive.exists():
        if shutil.disk_usage(archive.parent).free<6_000_000_000:raise RuntimeError('배포본 다운로드에 필요한 여유 공간 6GB가 없습니다.')
        url='https://www.kaggle.com/api/v1/datasets/download/cornell-university/arxiv/arxiv-metadata-oai-snapshot.json'
        temp=archive.with_suffix('.part');total=temp.stat().st_size if temp.exists() else 0
        headers={'User-Agent':'HaruNews/1.0'}
        if total:headers['Range']='bytes='+str(total)+'-'
        with urlopen(Request(url,headers=headers),timeout=60) as response:
            resume=response.status==206 and response.headers.get('Content-Range','').startswith('bytes '+str(total)+'-')
            if not resume:total=0
            out=temp.open('ab' if resume else 'wb')
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>6_000_000_000:raise RuntimeError('메타데이터 다운로드 6GB 한도 초과')
                out.write(chunk)
                if total%(100*1024*1024)==0:print(json.dumps({'download_mb':total//(1024*1024)}),flush=True)
            out.close()
        with temp.open('rb') as check:
            if not zipfile.is_zipfile(temp) and check.read(1)!=b'{':raise RuntimeError('Kaggle 메타데이터 JSON/ZIP 형식 오류')
        temp.replace(archive)
    with sqlite3.connect(path,timeout=30) as db:
        init(db);wanted={r[0] for r in db.execute('SELECT paper_id FROM arxiv_papers')}
    found=0;changed=0;scanned=0
    z=zipfile.ZipFile(archive) if zipfile.is_zipfile(archive) else None
    if z:
        info=z.getinfo('arxiv-metadata-oai-snapshot.json')
        if info.file_size>8_000_000_000:raise RuntimeError('압축 해제 크기 한도 초과')
    stream=z.open(info) if z else archive.open('rb')
    with stream,sqlite3.connect(path,timeout=30) as db:
        for line in stream:
            if len(line)>1_000_000:raise RuntimeError('메타데이터 행 크기 초과')
            row=json.loads(line);scanned+=1
            if row.get('id') in wanted:
                found+=1;changed+=int(save(db,snapshot_record(row)));db.commit()
            if scanned%250000==0:print(json.dumps({'scanned':scanned,'matched':found,'updated':changed}),flush=True)
    if z:z.close()
    report={'provider':'arxiv_kaggle','at':now(),'known':len(wanted),'matched':found,'updated':changed,'scanned':scanned,'archive':str(archive)}
    (ROOT/'.runtime/verification/arxiv-snapshot-import.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/news.sqlite3'));parser.add_argument('--archive',default=str(ROOT/'.runtime/papers/arxiv-metadata.snapshot'));a=parser.parse_args();run(a.db,a.archive)
