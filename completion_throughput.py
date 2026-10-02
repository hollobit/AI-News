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
            if self.successes >= 10 and stamp - self.last_decrease >= 30:
                self.target = min(self.maximum, self.target + 1)
                self.successes = 0
