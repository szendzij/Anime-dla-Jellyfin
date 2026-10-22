import io
import sys
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from kolekcja import State, build_handler  # noqa: E402


class DummySocket:
    def __init__(self, data: bytes):
        self._rfile = io.BytesIO(data)
        self._wfile = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self._wfile

    def sendall(self, data: bytes):
        self._wfile.write(data)


class DummyServer:
    def __init__(self):
        self.server_name = "localhost"
        self.server_port = 9100
        self.server_address = ("127.0.0.1", 9100)


class TestIngressAndRwd(unittest.TestCase):
    def setUp(self):
        self.state = State({}, {"series": {}}, Path("/dummy/collection.json"), Path("/dummy/root"), "http://localhost:9099")
        self.handler_cls = build_handler(self.state)

    def test_direct_root_returns_html(self):
        req = b"GET / HTTP/1.1\r\nHost: localhost:9100\r\n\r\n"
        sock = DummySocket(req)
        self.handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
        response = sock._wfile.getvalue().decode("utf-8", errors="replace")
        self.assertIn("HTTP/1.0 200", response)
        self.assertIn("<!doctype html>", response)
        self.assertIn("viewport-fit=cover", response)
        self.assertIn("Kolekcja Anime", response)

    def test_ingress_header_strips_prefix(self):
        # Home Assistant Ingress przekazuje X-Ingress-Path
        ingress_path = "/api/hassio_ingress/token_xyz"
        req = f"GET {ingress_path}/api/collection HTTP/1.1\r\nHost: localhost:9100\r\nX-Ingress-Path: {ingress_path}\r\n\r\n".encode()
        sock = DummySocket(req)
        self.handler_cls(sock, ("127.0.0.1", 12345), DummyServer())
        response = sock._wfile.getvalue().decode("utf-8", errors="replace")
        self.assertIn("HTTP/1.0 200", response)
        self.assertIn("application/json", response)


if __name__ == "__main__":
    unittest.main()
