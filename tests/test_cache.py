import sys
import time
import threading
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from streamd import LinkCache  # noqa: E402
from oa import Link  # noqa: E402


class DummySource:
    def __init__(self):
        self.call_log = []

    def links(self, ep_id: str):
        self.call_log.append(ep_id)
        if ep_id == "slow":
            time.sleep(0.3)
        return [Link("1080p", 1080, f"http://video/{ep_id}")]


class TestLinkCacheConcurrency(unittest.TestCase):
    def test_concurrent_requests_dont_starve(self):
        source = DummySource()
        cache = LinkCache(source)
        fast_duration = [0.0]

        def fetch_slow():
            cache.get("slow")

        def fetch_fast():
            time.sleep(0.05)
            start = time.monotonic()
            res = cache.get("fast")
            fast_duration[0] = time.monotonic() - start
            self.assertEqual(res, "http://video/fast")

        t1 = threading.Thread(target=fetch_slow)
        t2 = threading.Thread(target=fetch_fast)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Zapytanie 'fast' nie powinno czekać na zakończenie 'slow' (0.3s)
        self.assertLess(fast_duration[0], 0.20)

    def test_stampede_protection_same_key(self):
        source = DummySource()
        cache = LinkCache(source)

        def fetch():
            return cache.get("slow")

        t1 = threading.Thread(target=fetch)
        t2 = threading.Thread(target=fetch)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Dwa równoległe żądania na ten sam klucz powinny wywołać source.links tylko raz
        self.assertEqual(source.call_log.count("slow"), 1)


if __name__ == "__main__":
    unittest.main()
