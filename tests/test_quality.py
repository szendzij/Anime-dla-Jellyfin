import sys
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from oa import Link, pick_best  # noqa: E402
from providers.cda import video_id_from_url  # noqa: E402


class TestQualityAndCda(unittest.TestCase):
    def test_pick_best_exact_match(self):
        links = [Link("1080p", 1080, "http://1080"), Link("720p", 720, "http://720")]
        self.assertEqual(pick_best(links, 720).res, 720)

    def test_pick_best_fallback_when_preferred_missing(self):
        links = [Link("480p", 480, "http://480"), Link("360p", 360, "http://360")]
        # Użytkownik chce 720p, ale dostępne są tylko 480p i 360p -> wybiera najwyższy dostępny (480p)
        best = pick_best(links, 720)
        self.assertIsNotNone(best)
        self.assertEqual(best.res, 480)

        # Gdy dostępne są tylko wyższe (np. 1080p) -> fallback do 1080p
        links_high = [Link("1080p", 1080, "http://1080")]
        self.assertEqual(pick_best(links_high, 720).res, 1080)

    def test_pick_best_none_preferred_picks_highest(self):
        links = [Link("480p", 480, "http://480"), Link("1080p", 1080, "http://1080")]
        self.assertEqual(pick_best(links, None).res, 1080)

    def test_video_id_case_insensitive(self):
        self.assertEqual(video_id_from_url("https://www.cda.pl/video/284660ABC"), "284660abc")
        self.assertEqual(video_id_from_url("https://ebd.cda.pl/620x368/ABCDEF12"), "abcdef12")
        self.assertEqual(video_id_from_url("A1B2C3D4"), "a1b2c3d4")


if __name__ == "__main__":
    unittest.main()
