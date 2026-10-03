"""Admission-only backpressure; never cancels work or changes review decisions."""
import time


class AdaptiveAdmission:
    def __init__(self, maximum, enabled=False):
        self.maximum = maximum
        self.target = maximum
        self.enabled = enabled
        self.successes = 0
        self.last_decrease = float('-inf')

    def observe(self, code, stamp=None):
        if not self.enabled:
            return
        stamp = time.monotonic() if stamp is None else stamp
        if code in {'capacity', 'timeout', 'queue_timeout', 'network', 'database_locked'}:
            self.successes = 0
            # Concurrent failures from one wave count as one reduction.
            if stamp - self.last_decrease >= 30:
                self.target = max(1, self.target // 2)
                self.last_decrease = stamp
        elif code is None:
            self.successes += 1
            telemetry_current = getattr(self, 'telemetry_active', False) and stamp - self.last_sample <= 180
            if not telemetry_current and self.successes >= 10 and stamp - self.last_decrease >= 30:
                self.target = min(self.maximum, self.target + 1)
                self.successes = 0

    def tune(self, sample, stamp=None):
        """Two sustained windows before reducing admission; never cancel live work."""
        if not self.enabled or not sample or sample['calls'] < 5:return
        stamp = time.monotonic() if stamp is None else stamp
        if stamp - getattr(self, 'last_sample', float('-inf')) < 60:return
        previous = getattr(self, 'sample', None)
        self.last_sample = stamp
        self.sample = dict(sample)
        self.telemetry_active = True
        self.last_reason = 'observing'
        if stamp - self.last_decrease < 60:return
        pressure = sample['wait_seconds'] >= 8 and sample['wait_ratio'] >= .35 and sample['queued'] > 0
        prior_pressure = previous and previous['wait_seconds'] >= 8 and previous['wait_ratio'] >= .35 and previous['queued'] > 0
        # Compare accepted article throughput, not generated calls/rejected reviews.
        if pressure and prior_pressure and sample['completed_per_minute'] <= previous['completed_per_minute'] * 1.05:
            floor = max(1, self.maximum // 2)
            if self.target > floor:
                self.target -= 1
                self.last_decrease = stamp
                self.successes = 0
                self.last_reason = 'queue_pressure_without_throughput_gain'
        elif sample['queued'] <= 1 and sample['wait_seconds'] < 3 and sample['completed_per_minute'] > 0:
            if previous and previous['queued'] <= 1 and previous['wait_seconds'] < 3:
                self.target = min(self.maximum, self.target + 1)
                self.last_reason = 'low_wait_with_completed_articles'


def pressure_sample(path=None, *, owner_pid=None, stamp=None):
    """Read bounded, content-free telemetry for this driver; absent DB is no signal."""
    import os
    import sqlite3
    from pathlib import Path
    from contextlib import closing
    from llm_runtime import DEFAULT_PATH
    path = Path(path or os.environ.get('NEWS_LLM_RUNTIME_DB') or DEFAULT_PATH).resolve()
    if not path.exists():return None
    stamp = time.time() if stamp is None else stamp
    owner_pid = os.getpid() if owner_pid is None else owner_pid
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
            rows = db.execute('''SELECT wait_ms,run_ms FROM
                (SELECT * FROM llm_calls ORDER BY id DESC LIMIT 1000)
                WHERE owner_pid=? AND status='complete' AND finished_at>=?''', (owner_pid,stamp-120)).fetchall()
            queued = db.execute("SELECT count(*) FROM llm_calls WHERE owner_pid=? AND status='queued'",(owner_pid,)).fetchone()[0]
        wait = sum(r[0] or 0 for r in rows)
        run = sum(r[1] or 0 for r in rows)
        return dict(calls=len(rows),queued=queued,wait_seconds=wait/max(1,len(rows))/1000,wait_ratio=wait/max(1,wait+run))
    except sqlite3.Error:
        return None
