import json
import sys
import tempfile
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

import collection  # noqa: E402
from oa import Episode, Link  # noqa: E402
from providers import Source  # noqa: E402
from streamd import LinkCache  # noqa: E402


class MockSource(Source):
    def __init__(self, name="oa", prefix=""):
        super().__init__(name, name, None, prefix=prefix, needs_login=False)

    def episodes(self, ref: str) -> list[Episode]:
        return [
            Episode(title="Początek", ep_id="101", number=1, season=1),
            Episode(title="Ciąg dalszy", ep_id="102", number=2, season=1),
        ]

    def links(self, ep_id: str) -> list[Link]:
        return [
            Link(label="1080p", res=1080, src=f"http://cdn1/{ep_id}/1080.m3u8", main_url="https://cda.pl/video/1"),
            Link(label="1080p", res=1080, src=f"http://cdn2/{ep_id}/1080.mp4", main_url="https://vidoza.net/v/2"),
            Link(label="720p", res=720, src=f"http://cdn1/{ep_id}/720.mp4"),
        ]


class TestPlayerOverride(unittest.TestCase):
    def test_strm_url_with_player_idx(self):
        src = MockSource()
        url_default = src.strm_url("http://localhost:9099", "101")
        self.assertEqual(url_default, "http://localhost:9099/e/101")

        url_p1 = src.strm_url("http://localhost:9099", "101", player_idx=1)
        self.assertEqual(url_p1, "http://localhost:9099/e/101?p=1")

    def test_link_cache_player_selection(self):
        src = MockSource()
        cache = LinkCache(src)

        # Domyślny wybór (pick_best wybiera pierwszy 1080p)
        default_url = cache.get("101")
        self.assertEqual(default_url, "http://cdn1/101/1080.m3u8")

        # Wybór drugiego odtwarzacza (indeks 1)
        p1_url = cache.get("101", player_idx=1)
        self.assertEqual(p1_url, "http://cdn2/101/1080.mp4")

        # Wybór trzeciego odtwarzacza (indeks 2)
        p2_url = cache.get("101", player_idx=2)
        self.assertEqual(p2_url, "http://cdn1/101/720.mp4")

        # Poza zakresem -> fallback do pick_best
        fallback_url = cache.get("101", player_idx=99)
        self.assertEqual(fallback_url, "http://cdn1/101/1080.m3u8")

    def test_set_episode_player_updates_json_and_strm(self):
        src = MockSource()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "media"
            root.mkdir()
            coll_file = Path(td) / "collection.json"
            data = {"series": {}}

            collection.add_series(
                source=src,
                data=data,
                ref="test-anime",
                title="Test Anime",
                root=root,
                base_url="http://localhost:9099",
            )
            collection.save(coll_file, data)

            # Plik S01E01 powinien mieć domyślny URL
            strm_file = root / "Test Anime" / "Season 01" / "Test Anime - S01E01 - Początek.strm"
            self.assertTrue(strm_file.exists())
            self.assertEqual(strm_file.read_text(encoding="utf-8").strip(), "http://localhost:9099/e/101")

            # Ustawienie odtwarzacza 1 dla odcinka 101
            url = collection.set_episode_player(data, "test-anime", "101", 1, src)
            self.assertEqual(url, "http://localhost:9099/e/101?p=1")
            self.assertEqual(data["series"]["test-anime"]["player_overrides"]["101"], 1)

            # Plik .strm powinien być zaktualizowany
            self.assertEqual(strm_file.read_text(encoding="utf-8").strip(), "http://localhost:9099/e/101?p=1")

            # Reset do domyślnego (player_idx=None)
            url_reset = collection.set_episode_player(data, "test-anime", "101", None, src)
            self.assertEqual(url_reset, "http://localhost:9099/e/101")
            self.assertNotIn("101", data["series"]["test-anime"].get("player_overrides", {}))
            self.assertEqual(strm_file.read_text(encoding="utf-8").strip(), "http://localhost:9099/e/101")

    def test_kolekcja_endpoints(self):
        import io
        from kolekcja import State, build_handler

        class DummySocket:
            def __init__(self, data: bytes):
                self._rfile = io.BytesIO(data)
                self._wfile = io.BytesIO()

            def makefile(self, mode: str, *args, **kwargs):
                return self._rfile if "r" in mode else self._wfile

            def sendall(self, data: bytes):
                self._wfile.write(data)

        class DummyServer:
            server_name = "localhost"
            server_port = 9100
            server_address = ("127.0.0.1", 9100)

        src = MockSource()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "media"
            root.mkdir()
            coll_file = Path(td) / "collection.json"
            data = {"series": {}}
            collection.add_series(
                source=src,
                data=data,
                ref="test-anime",
                title="Test Anime",
                root=root,
                base_url="http://localhost:9099",
            )
            collection.save(coll_file, data)

            state = State({"oa": src}, data, coll_file, root, "http://localhost:9099")
            handler_cls = build_handler(state)

            # 1. GET /api/series_episodes?key=test-anime
            req = b"GET /api/series_episodes?key=test-anime HTTP/1.1\r\nHost: localhost:9100\r\n\r\n"
            sock = DummySocket(req)
            handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
            resp = sock._wfile.getvalue().decode("utf-8")
            self.assertIn("HTTP/1.0 200", resp)
            body = json.loads(resp.split("\r\n\r\n", 1)[1])
            self.assertEqual(body["key"], "test-anime")
            self.assertEqual(len(body["episodes"]), 2)
            self.assertEqual(body["episodes"][0]["ep_id"], "101")

            # 2. GET /api/episode_players?source=oa&ep_id=101
            req = b"GET /api/episode_players?source=oa&ep_id=101 HTTP/1.1\r\nHost: localhost:9100\r\n\r\n"
            sock = DummySocket(req)
            handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
            resp = sock._wfile.getvalue().decode("utf-8")
            self.assertIn("HTTP/1.0 200", resp)
            body = json.loads(resp.split("\r\n\r\n", 1)[1])
            self.assertEqual(len(body["players"]), 3)
            self.assertEqual(body["players"][1]["host"], "vidoza.net")

            # 3. POST /api/set_episode_player
            post_body = json.dumps({"key": "test-anime", "ep_id": "101", "player_idx": 1}).encode("utf-8")
            req = (
                b"POST /api/set_episode_player HTTP/1.1\r\nHost: localhost:9100\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(post_body)}\r\n\r\n".encode("utf-8")
                + post_body
            )
            sock = DummySocket(req)
            handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
            resp = sock._wfile.getvalue().decode("utf-8")
            self.assertIn("HTTP/1.0 200", resp)
            body = json.loads(resp.split("\r\n\r\n", 1)[1])
            self.assertTrue(body["ok"])
            self.assertEqual(body["url"], "http://localhost:9099/e/101?p=1")

            # Sprawdzenie czy plik na dysku ma nową zawartość
            strm_file = root / "Test Anime" / "Season 01" / "Test Anime - S01E01 - Początek.strm"
            self.assertEqual(strm_file.read_text(encoding="utf-8").strip(), "http://localhost:9099/e/101?p=1")

            # 4. POST /api/add (sprawdza czy logowanie liczby plików nie rzuca TypeError)
            add_body = json.dumps({"ref": "new-anime", "title": "New Anime", "source": "oa"}).encode("utf-8")
            req = (
                b"POST /api/add HTTP/1.1\r\nHost: localhost:9100\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(add_body)}\r\n\r\n".encode("utf-8")
                + add_body
            )
            sock = DummySocket(req)
            handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
            resp = sock._wfile.getvalue().decode("utf-8")
            self.assertIn("HTTP/1.0 200", resp)
            body = json.loads(resp.split("\r\n\r\n", 1)[1])
            self.assertEqual(body["title"], "New Anime")
            self.assertEqual(body["files"], 2)


if __name__ == "__main__":
    unittest.main()
