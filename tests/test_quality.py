import sys
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from oa import Link, pick_best  # noqa: E402
from providers.cda import Cda, dedupe_variants, video_id_from_url  # noqa: E402


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

    def test_variants_dash_with_separate_audio_points_to_m3u8(self):
        # Manifest DASH z osobnym audio (shaka-packager) musi odsyłać do HLS .m3u8,
        # ponieważ pliki wideo z BaseURL nie zawierają ścieżki dźwiękowej.
        mpd_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
          <Period id="0">
            <AdaptationSet id="0" contentType="video">
              <Representation id="0" mimeType="video/mp4" width="1280" height="720">
                <BaseURL>sdb123.mp4</BaseURL>
              </Representation>
              <Representation id="1" mimeType="video/mp4" width="1920" height="1080">
                <BaseURL>hdb123.mp4</BaseURL>
              </Representation>
            </AdaptationSet>
            <AdaptationSet id="1" contentType="audio">
              <Representation id="2" mimeType="audio/mp4">
                <BaseURL>a_hdb123.mp4</BaseURL>
              </Representation>
            </AdaptationSet>
          </Period>
        </MPD>"""
        cda = Cda()
        cda._page = lambda url: mpd_xml
        variants = dedupe_variants(cda._variants("https://cdn.cda.pl/token/1234/test.mpd"))
        self.assertEqual(len(variants), 2)
        # Najwyższy wariant pierwszy
        self.assertEqual(variants[0].res, 1080)
        self.assertEqual(variants[0].mime, "application/x-mpegURL")
        self.assertEqual(variants[0].src, "https://cdn.cda.pl/token/1234/test.m3u8")
        self.assertEqual(variants[1].res, 720)
        self.assertEqual(variants[1].mime, "application/x-mpegURL")
        self.assertEqual(variants[1].src, "https://cdn.cda.pl/token/1234/test.m3u8")

    def test_variants_dash_unified_audio_points_to_mp4(self):
        # Gdy manifest nie ma osobnego AdaptationSet audio, BaseURL jest kompletnym plikiem
        mpd_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
          <Period id="0">
            <AdaptationSet id="0" contentType="video">
              <Representation id="0" width="1280" height="720">
                <BaseURL>unified720.mp4</BaseURL>
              </Representation>
            </AdaptationSet>
          </Period>
        </MPD>"""
        cda = Cda()
        cda._page = lambda url: mpd_xml
        variants = dedupe_variants(cda._variants("https://cdn.cda.pl/token/1234/test.mpd"))
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].res, 720)
        self.assertEqual(variants[0].mime, "video/mp4")
        self.assertEqual(variants[0].src, "https://cdn.cda.pl/token/1234/unified720.mp4")


if __name__ == "__main__":
    unittest.main()
