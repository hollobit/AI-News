import sqlite3
from completion_idle import install,token,remember,unchanged


def test_idle_cursor_changes_with_sources_and_workflows_but_not_paper_queue(tmp_path):
    with sqlite3.connect(tmp_path/'db') as db:
        db.executescript('''CREATE TABLE articles(id INTEGER PRIMARY KEY,text TEXT);
            CREATE TABLE strategic_workflow_runs(id TEXT PRIMARY KEY,status TEXT,error TEXT);
            CREATE TABLE arxiv_paper_analyses(paper_id TEXT,status TEXT);
            INSERT INTO articles VALUES(1,'current');
            INSERT INTO strategic_workflow_runs VALUES('run','complete','');''')
        install(db);db.commit()
        initial=token(db);remember(db,'cycle',initial);db.commit()
        assert unchanged(db,'cycle')
        db.execute("INSERT INTO arxiv_paper_analyses VALUES('paper','running')");db.commit()
        assert unchanged(db,'cycle')
        db.execute("UPDATE strategic_workflow_runs SET error='revoked'");db.commit()
        assert not unchanged(db,'cycle')
        remember(db,'cycle',token(db));db.commit()
        assert unchanged(db,'cycle')
        before=token(db)
        db.execute("UPDATE articles SET text='new input'");db.commit()
        remember(db,'cycle',before);db.commit()
        assert not unchanged(db,'cycle')
