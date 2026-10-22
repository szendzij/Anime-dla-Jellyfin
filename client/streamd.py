#!/usr/bin/env python3
"""Redirector strumieni ogladajanime.pl dla Jellyfin.

Problem: linki do odcinków to HLS z tokenem ważnym kilka godzin. Plik .strm
z takim linkiem przestaje działać po wygaśnięciu tokenu.

Rozwiązanie: .strm wskazuje na ten serwer (`/e/<ep_id>`), a serwer przy każdym
żądaniu zwraca 302 na aktualny, świeży link z CDN. Token jest odświeżany
z wyprzedzeniem, wyliczonym z jego własnego znacznika czasu.

    GET /e/412634   -> 302 Location: https://s3-.../master.m3u8?token=...
    GET /health     -> 200 {"ok":true,"cached":N}

Uruchomienie:
    OA_LOGIN=... OA_PASS=... python3 streamd.py --port 9099
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

import requests

from oa import AuthError, OgladajAnimeError
from oa import UA as BROWSER_UA
from providers import CDA, OA, SHINDEN, ProviderError, build_sources, pick_best, split_ref

# CDA oddaje jeden plik MP4 na jakość i przepuszcza ffmpeg (UA Lavf), więc
# domyślnie przekierowujemy — bajty nie jadą przez HA. Gdyby CDN zaczął
# odrzucać ffmpeg, CDA_PROXY=1 przesyła strumień przez ten serwer.
CDA_PROXY = os.environ.get("CDA_PROXY", "").lower() in ("1", "true", "yes")

# Znacznik czasu w tokenie: ogladajanime "-<epoch>-", CDA "/<epoch>/".
TOKEN_EPOCH = re.compile(r"(?<!\d)(1\d{9})(?!\d)")

# CDN podaje segmenty HLS z rozszerzeniem .css, a ffmpeg odrzuca nieznane
# rozszerzenia ("not in allowed_segment_extensions"). Przepisujemy playlistę tak,
# żeby segmenty nazywały się .ts u nas, a my przekierowujemy do prawdziwego .css.
SEGMENT_PROXY = os.environ.get("OA_SEGMENT_PROXY", "").lower() in ("1", "true", "yes")


def b64e(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def b64d(value: str) -> str:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad).decode()
SAFETY_MARGIN = 600  # odśwież 10 min przed wygaśnięciem
DEFAULT_TTL = 3 * 3600  # gdy nie da się odczytać znacznika z tokenu


class LinkCache:
    """Cache linków per ep_id z odświeżaniem po wygaśnięciu tokenu."""

    def __init__(self, source, quality: int | None = None) -> None:
        self.source = source
        self.quality = quality
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._fetching: dict[str, threading.Event] = {}

    @staticmethod
    def _expiry(url: str) -> float:
        m = TOKEN_EPOCH.search(url)
        if m:
            return int(m.group(1)) - SAFETY_MARGIN
        return time.time() + DEFAULT_TTL

    def get(self, ep_id: str) -> str | None:
        with self._lock:
            hit = self._data.get(ep_id)
            if hit and hit[1] > time.time():
                return hit[0]

            event = self._fetching.get(ep_id)
            is_fetcher = False
            if event is None:
                event = threading.Event()
                self._fetching[ep_id] = event
                is_fetcher = True

        if not is_fetcher:
            event.wait(timeout=45)
            with self._lock:
                hit = self._data.get(ep_id)
                return hit[0] if hit else None

        url = None
        try:
            links = self.source.links(ep_id)
            link = pick_best(links, self.quality)
            if link is not None:
                url = link.src
        finally:
            with self._lock:
                if url:
                    self._data[ep_id] = (url, self._expiry(url))
                else:
                    self._data.pop(ep_id, None)
                self._fetching.pop(ep_id, None)
                event.set()

        return url

    def size(self) -> int:
        with self._lock:
            return len(self._data)


def make_handler(caches: dict, series_hint: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OAStream/1.0"

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host(self) -> str:
            return self.headers.get("Host") or "%s:%s" % self.server.server_address

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")

            if path in ("/health", ""):
                self._json(
                    200,
                    {
                        "ok": True,
                        "cached": {name: c.size() for name, c in caches.items()},
                    },
                )
                return

            mp = re.fullmatch(r"/p/([A-Za-z0-9_\-=]+)\.m3u8", path)
            if mp:
                self._playlist(b64d(mp.group(1)))
                return

            ms = re.fullmatch(r"/s/([A-Za-z0-9_\-=]+)\.ts", path)
            if ms:
                self._segment(b64d(ms.group(1)))
                return

            m = re.fullmatch(r"/e/([A-Za-z0-9_-]+)", path)
            if not m:
                self._json(404, {"error": "nieznana ścieżka", "oczekiwano": "/e/<ep_id>"})
                return

            name, ep_id = split_ref(m.group(1))
            try:
                url = caches[name].get(ep_id)
            except AuthError as e:
                self._json(502, {"error": f"logowanie: {e}"})
                return
            except ProviderError as e:
                self._json(502, {"error": str(e)})
                return

            if not url:
                self._json(404, {"error": f"odcinek {m.group(1)} nie ma dostępnych źródeł"})
                return

            if name in (CDA, SHINDEN):
                # CDA oddaje jeden plik na jakość (albo HLS z normalnymi .ts),
                # a Shinden rozwiązujemy do CDA — w obu przypadkach ffmpeg pobiera
                # materiał sam, więc przekierowujemy i nie przepuszczamy bajtów.
                if CDA_PROXY:
                    self._proxy(url, referer="https://www.cda.pl/")
                    return
                self.send_response(302)
                self.send_header("Location", url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            # HLS: przekierowanie (ffmpeg sam pobiera playlistę i segmenty, zero transferu przez nas).
            # Bramki typu vkuser.net: proxy z UA przeglądarki — bez tego dostają 400 od ffmpeg.
            if ".m3u8" in url:
                # nie przekierowujemy na CDN — tam playlista wskazuje segmenty .css
                # i ffmpeg je odrzuca. Podajemy własną, przepisaną playlistę.
                self.send_response(302)
                self.send_header("Location", "http://%s/p/%s.m3u8" % (self._host(), b64e(url)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            self._proxy(url)

        def _fetch_text(self, url: str, referer: str = "https://ogladajanime.pl/") -> tuple[str, str] | None:
            fwd = {"User-Agent": BROWSER_UA, "Referer": referer}
            try:
                r = requests.get(url, headers=fwd, timeout=(10, 30), allow_redirects=True)
            except requests.RequestException as e:
                self._json(502, {"error": f"playlista: {e}"})
                return None
            if r.status_code != 200:
                self._json(502, {"error": f"playlista HTTP {r.status_code}"})
                return None
            return r.text, r.url

        def _playlist(self, upstream: str) -> None:
            got = self._fetch_text(upstream)
            if not got:
                return
            text, base = got
            me = "http://%s" % self._host()

            def rewrite_attr(m: re.Match) -> str:
                target = urljoin(base, m.group(1))
                if target.split("?")[0].endswith(".m3u8"):
                    return 'URI="%s/p/%s.m3u8"' % (me, b64e(target))
                return 'URI="%s/s/%s.ts"' % (me, b64e(target))

            out: list[str] = []
            for line in text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    out.append(re.sub(r'URI="([^"]+)"', rewrite_attr, line))
                    continue
                target = urljoin(base, s)
                if target.split("?")[0].endswith(".m3u8"):
                    out.append("%s/p/%s.m3u8" % (me, b64e(target)))
                else:
                    out.append("%s/s/%s.ts" % (me, b64e(target)))
            body = ("\n".join(out) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _segment(self, upstream: str) -> None:
            if SEGMENT_PROXY:
                self._proxy(upstream)
                return
            self.send_response(302)
            self.send_header("Location", upstream)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _proxy(self, url: str, referer: str = "https://ogladajanime.pl/") -> None:
            fwd = {"User-Agent": BROWSER_UA, "Referer": referer}
            rng = self.headers.get("Range")
            if rng:
                fwd["Range"] = rng
            try:
                with requests.get(url, headers=fwd, stream=True, timeout=(10, 60), allow_redirects=True) as up:
                    if up.status_code not in (200, 206):
                        self._json(502, {"error": f"źródło HTTP {up.status_code}"})
                        return
                    self.send_response(up.status_code)
                    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                        if up.headers.get(h):
                            self.send_header(h, up.headers[h])
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    for chunk in up.iter_content(chunk_size=1 << 20):
                        if chunk:
                            self.wfile.write(chunk)
            except (requests.RequestException, ConnectionError) as e:
                # Klient przerwał odbiór — tak wygląda zatrzymanie odtwarzania
                # w Jellyfinie i przewijanie. ConnectionError obejmuje zarówno
                # BrokenPipeError, jak i ConnectionResetError; bez tego
                # socketserver wypisuje cały traceback do logu dodatku.
                print(f"klient przerwał transmisję ({e.__class__.__name__})", flush=True)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="redirector strumieni dla Jellyfin")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--quality", type=int, default=None, help="wymuś np. 1080")
    ap.add_argument("--session-file", default=None)
    args = ap.parse_args()

    kw = {"session_file": args.session_file} if args.session_file else {}
    sources = build_sources(quality=args.quality, **kw)
    try:
        sources[OA].ensure_ready()
        print("ogladajanime: zalogowano", flush=True)
    except (AuthError, OgladajAnimeError) as e:
        # CDA działa bez logowania — brak danych ogladajanime nie blokuje startu,
        # odcinki z tego źródła odpowiedzą wtedy 502 z powodem.
        print(f"ogladajanime: pominięte ({e})", flush=True)

    caches = {name: LinkCache(src, args.quality) for name, src in sources.items()}
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), make_handler(caches, None))
    except OSError as e:
        # Najczęstsza przyczyna: druga instancja albo port zajęty przez sąsiada.
        # Bez tego log pokazuje surowy traceback, a supervisor restartuje w pętli.
        print(
            f"nie mogę zająć portu {args.host}:{args.port} ({e}). "
            f"Sprawdź, czy coś już nasłuchuje: ss -lntp | grep {args.port}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(f"nasłuchuję na http://{args.host}:{args.port}  (np. /e/412634)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nzatrzymano", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
