import sys
import tempfile
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

import collection  # noqa: E402
from kolekcja import State  # noqa: E402


class TestCollectionSync(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp_dir.name) / "collection.json"
        self.root = Path(self.tmp_dir.name) / "media"
        self.root.mkdir()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_state_reloads_external_changes(self):
        # Inicjalny stan
        initial_data = {"series": {"s1": {"title": "Series 1", "source": "oa"}}}
        collection.save(self.path, initial_data)

        st = State({}, collection.load(self.path), self.path, self.root, "http://localhost:9099")
        self.assertIn("s1", st.get_data()["series"])

        # Zmiana z zewnętrznego procesu (np. sync.py)
        external_data = {
            "series": {
                "s1": {"title": "Series 1", "source": "oa", "last_sync": "2026-10-22"},
                "s2": {"title": "Series 2", "source": "cda"},
            }
        }
        collection.save(self.path, external_data)

        # State powinien dynamicznie zwrócić zaktualizowaną zawartość
        reloaded = st.get_data()
        self.assertIn("s2", reloaded["series"])
        self.assertEqual(reloaded["series"]["s1"].get("last_sync"), "2026-10-22")

    def test_trigger_scan_status_missing_vars(self):
        import os
        old_url = os.environ.pop("JELLYFIN_URL", None)
        old_key = os.environ.pop("JELLYFIN_API_KEY", None)
        try:
            ok, msg = collection.trigger_scan_status()
            self.assertFalse(ok)
            self.assertIn("JELLYFIN_URL", msg)

            ok, msg = collection.trigger_scan_status(url="http://jellyfin:8096")
            self.assertFalse(ok)
            self.assertIn("JELLYFIN_API_KEY", msg)
        finally:
            if old_url:
                os.environ["JELLYFIN_URL"] = old_url
            if old_key:
                os.environ["JELLYFIN_API_KEY"] = old_key

    def test_trigger_scan_status_success_and_error(self):
        from unittest.mock import patch, MagicMock
        import requests

        with patch("collection.requests.post") as mock_post:
            # 204 Success
            mock_post.return_value = MagicMock(status_code=204)
            ok, msg = collection.trigger_scan_status("http://example.com", "secret")
            self.assertTrue(ok)
            self.assertIn("został uruchomiony", msg)

            # 401 Auth error
            mock_post.return_value = MagicMock(status_code=401)
            ok, msg = collection.trigger_scan_status("http://example.com", "secret")
            self.assertFalse(ok)
            self.assertIn("Błąd autoryzacji", msg)

            # Connection exception
            mock_post.side_effect = requests.RequestException("connection timeout")
            ok, msg = collection.trigger_scan_status("http://example.com", "secret")
            self.assertFalse(ok)
            self.assertIn("Błąd połączenia", msg)


if __name__ == "__main__":
    unittest.main()
