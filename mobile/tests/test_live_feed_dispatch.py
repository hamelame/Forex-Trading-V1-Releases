"""Regression checks for the public live-feed scheduling lock."""
import threading
import unittest
from concurrent.futures import Future
from forex_app.live_market import LiveMarketFeed


class CompletedExecutor:
    """Represents the already-finished Future race on a fast/blocked provider."""
    def submit(self, fn, *args):
        f = Future()
        f.set_result(None)
        return f


class PendingExecutor:
    def __init__(self):
        self.submissions=[]
    def submit(self, fn, *args):
        future=Future()
        self.submissions.append(future)
        return future


class FailedExecutor:
    def submit(self, fn, *args):
        raise RuntimeError('executor unavailable')


class LiveFeedDispatchTests(unittest.TestCase):
    def feed(self):
        f=LiveMarketFeed(['EURUSD'],cfg={'live_data_submit_batch':2})
        f._executor.shutdown(wait=False)
        return f

    def test_immediately_completed_future_no_deadlock(self):
        feed=self.feed()
        feed._executor=CompletedExecutor()
        finished=threading.Event()
        t=threading.Thread(target=lambda:(feed.advance(),finished.set()),daemon=True)
        t.start()
        self.assertTrue(finished.wait(2),'advance() blocked on reentrant callback lock')
        self.assertFalse(feed._inflight)

    def test_pending_task_is_not_resubmitted(self):
        feed=self.feed()
        executor=PendingExecutor()
        feed._executor=executor
        feed.advance()
        feed.advance()
        self.assertEqual(len(executor.submissions),1)
        self.assertEqual(feed._inflight,{'EURUSD'})
        executor.submissions[0].set_result(None)
        self.assertFalse(feed._inflight)

    def test_submit_failure_releases_reservation(self):
        feed=self.feed()
        feed._executor=FailedExecutor()
        feed.advance()
        self.assertFalse(feed._inflight)
        self.assertIn('Fetch scheduling failed',feed._last_error['EURUSD'])


if __name__=='__main__':
    unittest.main()
