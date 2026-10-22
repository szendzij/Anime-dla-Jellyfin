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


if __name__ == "__main__":
    unittest.main()
