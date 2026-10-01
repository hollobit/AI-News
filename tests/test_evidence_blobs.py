import json
import sqlite3
import pytest
from evidence_blobs import init,dumps,loads,migrate_batch


def test_shared_long_text_lossless_legacy_and_corruption():
    db=sqlite3.connect(':memory:');init(db)
    text='원문 인용 %2f\n'*200
    a={'id':'a','evidence':[{'text':text}]};b={'id':'b','evidence':[{'text':text}]}
    aa=dumps(db,a);bb=dumps(db,b)
    assert loads(db,aa)==a and loads(db,bb)==b
    assert loads(db,json.dumps(a))==a
    assert db.execute('SELECT count(*) FROM evidence_blobs').fetchone()[0]==3
    db.execute('DELETE FROM evidence_blobs')
    with pytest.raises(ValueError,match='Missing'):loads(db,aa)


def test_migration_is_bounded_resumable_and_preserves_history():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE improvement_catalog_history(payload_json TEXT)')
    originals=[{'version':i,'evidence':['same evidence '*100]} for i in range(3)]
    db.executemany('INSERT INTO improvement_catalog_history VALUES(?)',[(json.dumps(v),) for v in originals])
    with db: assert migrate_batch(db,'improvement_catalog_history','payload_json',2)['converted']==2
    with db: assert migrate_batch(db,'improvement_catalog_history','payload_json',2)['converted']==1
    assert migrate_batch(db,'improvement_catalog_history','payload_json',2)['scanned']==0
    assert [loads(db,r[0]) for r in db.execute('SELECT payload_json FROM improvement_catalog_history')]==originals
