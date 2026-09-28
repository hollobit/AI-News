import sqlite3
from service_supervisor import matching_processes,deep_command


def test_exact_process_matching_excludes_tools_other_projects_and_wrapper():
    output='''1 .venv/bin/python app.py serve --port 8001
2 .venv/bin/python app.py collect
3 .venv/bin/python corpus_completion.py --cycle c
4 /bin/bash -c python app.py serve
5 python /another/project/app.py serve
6 python service_supervisor.py server'''
    assert matching_processes('server',output)==[1]
    assert matching_processes('collector',output)==[2]
    assert matching_processes('deep',output)==[3]


def test_deep_never_resumes_user_pause_or_completed_cycle():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE rsi_cycles(id,status,pause_requested,owner_pid,created_at)')
    db.execute("INSERT INTO rsi_cycles VALUES('c','paused',1,NULL,'now')")
    assert deep_command(db) is None
    db.execute("UPDATE rsi_cycles SET status='running'")
    assert deep_command(db) is None
    db.execute('UPDATE rsi_cycles SET pause_requested=0')
    assert '--cycle' in deep_command(db)
    db.execute("UPDATE rsi_cycles SET status='complete'")
    assert deep_command(db) is None
