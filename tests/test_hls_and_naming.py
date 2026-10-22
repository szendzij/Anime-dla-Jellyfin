import sys
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from providers.cda import episode_number  # noqa: E402
from oa import Episode  # noqa: E402
from jellyfin import episode_filename  # noqa: E402


class TestNamingHeuristics(unittest.TestCase):
    def test_labeled_episode_number(self):
        self.assertEqual(episode_number("Odcinek 12"), 12)
        self.assertEqual(episode_number("Super Hero odc. 05"), 5)
        self.assertEqual(episode_number("Dragon Ball E03"), 3)
        self.assertEqual(episode_number("Naruto (07)"), 7)

    def test_year_at_end_not_treated_as_episode(self):
        # Tytuły filmów z rokiem (1950-2040) bez oznaczenia odcinka nie powinny dawać numeru odcinka
        self.assertIsNone(episode_number("Dragon Ball Z Film 1995"))
        self.assertIsNone(episode_number("Ghost in the Shell 1995"))
        self.assertIsNone(episode_number("Akira 1988"))
        self.assertIsNone(episode_number("Cyberpunk 2077"))

    def test_episode_filename_handles_none_number(self):
        ep = Episode(title="Zapowiedź", ep_id="123", number=None, season=0)
        filename = episode_filename("Tytuł Serii", ep)
        self.assertIn("S00E00", filename)
        self.assertIn("Zapowiedź", filename)


if __name__ == "__main__":
    unittest.main()
