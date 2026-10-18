"""Transactional input changes wake the existing wiki worker, never reset jobs."""
import json
import math
import sqlite3
import time
import uuid

FIELDS = {
    'articles': None,  # news_source binds the complete article row.
    'source_health': ('url', 'hash', 'last_status'),
    'source_versions': ('url', 'hash', 'title', 'text'),
    'arxiv_papers': ('paper_id', 'metadata_json', 'status'),
    'arxiv_paper_analyses': ('paper_id', 'input_hash', 'status', 'error', 'result_json'),
}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def install(db):
    """One bounded counter, changed in the same transaction as its inputs."""
    db.execute('''CREATE TABLE IF NOT EXISTS wiki_input_revision(
        id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,article_revision INTEGER NOT NULL DEFAULT 0,
        layout TEXT NOT NULL DEFAULT '')''')
    if 'article_revision' not in {r[1] for r in db.execute('PRAGMA table_info(wiki_input_revision)')}:
        db.execute('ALTER TABLE wiki_input_revision ADD COLUMN article_revision INTEGER NOT NULL DEFAULT 0')
    db.execute('INSERT OR IGNORE INTO wiki_input_revision(id,epoch) VALUES(1,?)', (uuid.uuid4().hex,))
    existing = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
    layout = {}
    for table, wanted in FIELDS.items():
        columns = tuple(r[1] for r in db.execute('PRAGMA table_info(' + quoted(table) + ')'))
        fields = columns if wanted is None else tuple(c for c in wanted if c in columns)
        if fields:
            layout[table] = fields
    changed = False
    for table, fields in layout.items():
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'wiki_input_v1_' + table + '_' + event.lower()
            condition = ''
            if event == 'UPDATE':
                condition = '(' + ' OR '.join('OLD.' + quoted(c) + ' IS NOT NEW.' + quoted(c) for c in fields) + ')'
            if table == 'source_versions' and {'url', 'hash'} <= set(layout.get('source_health', ())):
                refs = ('OLD', 'NEW') if event == 'UPDATE' else ('OLD',) if event == 'DELETE' else ('NEW',)
                selected = ' OR '.join('EXISTS(SELECT 1 FROM source_health h WHERE h.url=' + p + '.url AND h.hash=' + p + '.hash)' for p in refs)
                condition = (condition + ' AND ' if condition else '') + '(' + selected + ')'
            sql = ('CREATE TRIGGER ' + name + ' AFTER ' + event + ' ON ' + quoted(table)
                   + (' WHEN ' + condition if condition else '')
                   + ' BEGIN UPDATE wiki_input_revision SET revision=revision+1'
                   + (',article_revision=article_revision+1' if table == 'articles' else '')
                   + ' WHERE id=1; END')
            if existing.get(name) != sql:
                db.execute('DROP TRIGGER IF EXISTS ' + name)
                db.execute(sql)
                changed = True
    encoded = json.dumps(layout, sort_keys=True)
    old = db.execute('SELECT layout FROM wiki_input_revision WHERE id=1').fetchone()[0]
    if changed or old != encoded:
        # Also catches a tracked table removed/recreated between polls.
        db.execute('UPDATE wiki_input_revision SET revision=revision+1,layout=? WHERE id=1', (encoded,))


class InputWatch:
    def __init__(self, *, quiet_seconds=10, article_wait_seconds=20, max_wait_seconds=45, clock=time.monotonic, wall=time.time):
        if not 0 < quiet_seconds <= max_wait_seconds or not 0 <= article_wait_seconds <= max_wait_seconds:
            raise ValueError('Invalid wiki update coalescing interval')
        self.quiet_seconds, self.max_wait_seconds = quiet_seconds, max_wait_seconds
        self.article_wait_seconds = article_wait_seconds
        self.clock, self.wall = clock, wall
        self.schema = self.token = self.first_change = self.last_change = None
        self.article_not_before = None
        self.last_notified_at = self.checked_at = None
        self.notifications = 0
        self.error_type = None
        self.enabled = True

    def poll(self, db, *, enabled=True):
        self.enabled = enabled
        self.checked_at = self.wall()
        now = self.clock()
        try:
            schema = db.execute('PRAGMA schema_version').fetchone()[0]
            if schema != self.schema:
                install(db)
                self.schema = db.execute('PRAGMA schema_version').fetchone()[0]
            token = tuple(db.execute('SELECT epoch,revision,article_revision FROM wiki_input_revision WHERE id=1').fetchone())
        except sqlite3.Error as error:
            self.schema = None
            self.error_type = type(error).__name__
            return False
        self.error_type = None
        if self.token is not None and token != self.token and enabled:
            if self.first_change is None:
                self.first_change = now
            self.last_change = now
            if token[0] != self.token[0] or token[2] != self.token[2]:
                # Give fetched originals a chance to join a Telegram extraction.
                self.article_not_before = now + self.article_wait_seconds
        self.token = token
        if not enabled:
            self.first_change = self.last_change = None
            self.article_not_before = None
        if self.first_change is None:
            return False
        if now < self._deadline():
            return False
        self.first_change = self.last_change = None
        self.article_not_before = None
        self.notifications += 1
        self.last_notified_at = self.wall()
        return True

    def _deadline(self):
        return min(max(self.last_change + self.quiet_seconds, self.article_not_before or 0),
                   self.first_change + self.max_wait_seconds)

    def status(self):
        state = ('unavailable' if self.error_type else 'disabled' if not self.enabled
                 else 'coalescing' if self.first_change is not None else 'watching')
        value = dict(state=state, quiet_seconds=self.quiet_seconds, article_wait_seconds=self.article_wait_seconds,
                     max_wait_seconds=self.max_wait_seconds,
                     checked_at=self.checked_at, last_notified_at=self.last_notified_at,
                     notifications=self.notifications, error_type=self.error_type)
        if self.first_change is not None:
            deadline = self._deadline()
            value['remaining_seconds'] = max(0, math.ceil(deadline - self.clock()))
        return value


def notify(db, watch, wiki):
    """Send only an event; compile still checks enabled/current/owner/retry gates."""
    if watch.poll(db, enabled=wiki.enabled):
        wiki.wake.set()


def status_for_view(worker):
    """Small, content-free diagnostics for the operations flow."""
    value = worker.get('wiki_source_updates')
    if not isinstance(value, dict):
        return None
    fields = ('state', 'quiet_seconds', 'article_wait_seconds', 'max_wait_seconds', 'last_notified_at',
              'notifications', 'remaining_seconds', 'error_type')
    result = {k:value[k] for k in fields if k in value}
    if worker.get('status') != 'running' or worker.get('health') == 'unresponsive':
        result.update(state='unavailable', error_type='WorkerUnavailable')
    return result
