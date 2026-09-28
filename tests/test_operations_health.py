import sqlite3
from operations_health import audit

def test_health_detects_stale_collector_orphan_and_review_without_mutation(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE state(key TEXT,value TEXT);
        INSERT INTO state VALUES('collector_last_success','2026-09-19T00:00:00+00:00');
        CREATE TABLE bulk_baseline_runs(status TEXT,owner_pid INTEGER,updated_at TEXT,created_at TEXT);
        INSERT INTO bulk_baseline_runs VALUES('running',NULL,'2026-09-19','2026-09-19');
        CREATE TABLE rsi_cycles(status TEXT,owner_pid INTEGER,updated_at TEXT,created_at TEXT);
        INSERT INTO rsi_cycles VALUES('paused',NULL,'2026-09-19','2026-09-19');
        CREATE TABLE arxiv_paper_analyses(status TEXT);
        INSERT INTO arxiv_paper_analyses VALUES('needs_review');""")
    report=audit(path,2000000000)
    assert len(report['alerts'])==4
    assert report['deep']['status']=='paused'
    with sqlite3.connect(path) as db:assert db.execute('SELECT status FROM rsi_cycles').fetchone()[0]=='paused'
