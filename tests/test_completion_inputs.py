import json
from corpus_completion import CompletionRunner,connect
from completion_inputs import reconcile,refresh
from tests import test_corpus_completion as fixtures


def test_live_admission_preserves_retries_and_defers_running_revision():
    case=fixtures.CompletionTests();case.setUp()
    try:
        runner=CompletionRunner(case.path,'cycle',batch_size=1,recent_first=True);runner.prepare()
        planned=runner.plan();active=planned['snapshot']['identities'][0]
        changed=[dict(i,text='changed observation') if i['source_url']==active else i for i in case.items]
        changed.append(dict(case.items[0],source_url='https://example.com/new'))
        with connect(case.path) as db:
            db.execute("UPDATE corpus_completion_documents SET attempts=3,status='needs_review' WHERE document_id!=?",(active,))
            result=reconcile(db,'cycle',changed)
            assert result=={'added':1,'changed':0,'deferred':1}
            assert db.execute("SELECT count(*) FROM corpus_completion_documents WHERE attempts=3").fetchone()[0]==5
            assert db.execute("SELECT status,attempts FROM corpus_completion_documents WHERE document_id=?",(active,)).fetchone()[:]==('running',1)
            db.execute("UPDATE rsi_rounds SET status='complete'")
            result=reconcile(db,'cycle',changed)
            assert result=={'added':0,'changed':1,'deferred':0}
            assert json.loads(db.execute('SELECT prior_json FROM completion_input_history').fetchone()[0])['attempts']==1
            db.execute("UPDATE rsi_cycles SET pause_requested=1")
            assert reconcile(db,'cycle',changed)['paused']
    finally:case.tearDown()
