from unittest.mock import patch
import pipeline_health


def test_watch_uses_single_lease_and_stops_between_ticks(tmp_path):
    (tmp_path/'.runtime').mkdir()
    class Stop:
        stopped = False
        waits = []
        def is_set(self): return self.stopped
        def set(self): self.stopped = True
        def wait(self, seconds):
            self.waits.append(seconds)
            self.stopped = True
    stop = Stop()
    with patch.object(pipeline_health, 'ROOT', tmp_path), patch('threading.Event', return_value=stop), patch('signal.signal'), patch.object(pipeline_health, 'run') as run:
        pipeline_health.watch()
        run.assert_called_once_with(export=True, log=True)
        assert 0 < stop.waits[0] <= 2
        import fcntl
        with (tmp_path/'.runtime/operations-health-watch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run.reset_mock()
            stop.stopped = False
            pipeline_health.watch()
            run.assert_not_called()
