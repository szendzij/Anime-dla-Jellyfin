"""Provider Shinden.pl — katalog odcinków i agregator odtwarzaczy.

Shinden nie hostuje wideo: zbiera odcinki i linkuje do zewnętrznych serwisów.
Rozpoznanie (2026-10, sprawdzone na żywo):

    GET  /titles?search=<fraza>            -> wyniki (to jest wyszukiwarka; /search?query= to tylko strona)
    GET  /series/<id>-<slug>/all-episodes  -> tabela odcinków (data-episode-no, .ep-title)
    GET  /episode/<seria>/view/<ep_id>     -> lista playerów w JSON `data-episode`
    GET  api4.shinden.pl/xhr/<online_id>/player_load?auth=<token>
    ... 5 s przerwy ...
    GET  api4.shinden.pl/xhr/<online_id>/player_show?auth=<token>&width=0&height=-1
                                           -> HTML z <iframe> do serwisu z wideo

Token `auth` to gościnny `_Storage.basic` wzięty ze strony odcinka; do 5 sekund
przerwy jest obowiązkowe — bez niej Shinden odpowiada 403 „Błędne wejście".

Działa bez logowania i wystarcza do tego zwykłe `requests` (blokada, na którą
wcześniej trafialiśmy z curl-em, nie dotyczy Pythona).

Obsługiwane źródła wideo: **CDA** (najczęstszy player, ok. połowy pozycji) —
jego adres `ebd.cda.pl/<w>x<h>/<id>` rozwiązujemy istniejącym providerem CDA.
Pozostałe (Filemoon, voe, Vidara, Mega, Crunchyroll) są JS-owe, na martwych
domenach albo objęte DRM — te odcinki zgłaszamy jako nieobsługiwane.
"""

from __future__ import annotations

import base64
import html as html_mod
import os
import re
import time

import requests

from oa import Episode, Link, UA
from providers.cda import Cda, CdaError, video_id_from_url

BASE = "https://shinden.pl"
XHR = "https://api4.shinden.pl/xhr"

EP_PREFIX = "sh-"

# Shinden przygotowuje odtwarzacz asynchronicznie; krótsza przerwa to 403.
PLAYER_WAIT = float(os.environ.get("SHINDEN_PLAYER_WAIT", "5"))
PLAYER_ATTEMPTS = int(os.environ.get("SHINDEN_PLAYER_ATTEMPTS", "3"))

RE_EP_ROW = re.compile(r'<tr[^>]*data-episode-no="(\d+)"[^>]*>(.*?)</tr>', re.S | re.I)
RE_EP_TITLE = re.compile(r'class="[^"]*ep-title[^"]*"[^>]*>(.*?)</td>', re.S)
RE_EP_HREF = re.compile(r'href="(/episode/[^"]+/view/\d+)"')
RE_SERIES_HREF = re.compile(r'href="(/series/\d+-[^"]+)"')
RE_IMG = re.compile(r'<img[^>]+(?:data-src|src)="([^"]+)"', re.I)
RE_PLAYER_JSON = re.compile(r"data-episode='([^']+)'")
RE_AUTH = re.compile(r"_Storage\.basic\s*=\s*'([^']+)'")
RE_IFRAME = re.compile(r'<iframe[^>]*src="([^"]+)"', re.I)


class ShindenError(RuntimeError):
    pass


def b64e(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def b64d(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


def _clean(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def parse_auth(page: str) -> str:
    """Token gościa ze strony — w postaci surowej.

    Nie kodujemy go tutaj: wartość trafia do `params=`, a `requests` zakoduje ją
    raz. Podanie już zakodowanego `%3D` dawało `%253D` i Shinden odrzucał żądanie
    jako „Błędne wejście".
    """
    m = RE_AUTH.search(page)
    if not m:
        raise ShindenError("nie znaleziono tokenu gościa (_Storage.basic) na stronie")
    return m.group(1)


def parse_players(page: str) -> list[dict]:
    """Playery odcinka: lista słowników z JSON-a `data-episode`."""
    import json

    out: list[dict] = []
    seen: set[str] = set()
    for blob in RE_PLAYER_JSON.findall(page):
        try:
            data = json.loads(html_mod.unescape(blob))
        except ValueError:
            continue
        oid = str(data.get("online_id") or "")
        if oid and oid not in seen:
            seen.add(oid)
            out.append(data)
    return out


def order_players(players: list[dict]) -> list[dict]:
    """Kolejność prób: CDA najpierw, potem polskie napisy i wyższa jakość.

    Obsługujemy tylko CDA, więc reszta trafia na koniec — dzięki temu nie
    tracimy 5 sekund na serwis, którego i tak nie odczytamy.
    """

    def rank(p: dict) -> tuple:
        name = str(p.get("player") or "").lower()
        res = int(re.sub(r"\D", "", str(p.get("max_res") or "0")) or 0)
        return (
            0 if name.startswith("cda") else 1,
            0 if str(p.get("lang_subs") or "").lower() == "pl" else 1,
            -res,
        )

    return sorted(players, key=rank)


def parse_search(page: str) -> list[dict]:
    """Wyniki z /titles: linki do serii z tytułem i okładką."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in RE_SERIES_HREF.finditer(page):
        ref = m.group(1)
        slug = ref.rsplit("/", 1)[-1]
        if ref in seen or "add" in slug:
            continue
        seen.add(ref)
        window = page[m.start() : m.start() + 900]
        title = _clean(m.group(0))
        anchor = re.search(r'href="' + re.escape(ref) + r'"[^>]*>(.*?)</a>', page, re.S)
        if anchor:
            title = _clean(anchor.group(1))
        if not title or len(title) < 2:
            title = slug.replace("-", " ").strip().title()
        img = RE_IMG.search(window)
        poster = img.group(1) if img else ""
        if poster.startswith("//"):
            poster = "https:" + poster
        out.append({"ref": ref, "title": title, "poster": poster, "kind": "series"})
    return out


def parse_episodes(page: str) -> list[Episode]:
    """Tabela odcinków -> Episode. Seria jest wypisana od najnowszego."""
    out: list[Episode] = []
    used: set[int] = set()
    special_no = 0

    for raw_no, body in RE_EP_ROW.findall(page):
        href = RE_EP_HREF.search(body)
        if not href:
            continue
        title_m = RE_EP_TITLE.search(body)
        title = _clean(title_m.group(1) if title_m else "")
        number = int(raw_no)
        if number in used:
            special_no += 1
            out.append(Episode(title=title or f"Materiał dodatkowy {special_no}",
                               ep_id=b64e(href.group(1)), number=special_no, season=0))
            continue
        used.add(number)
        out.append(Episode(title=title or f"Odcinek {number}",
                           ep_id=b64e(href.group(1)), number=number))

    if out:
        out.sort(key=lambda e: (e.season, e.number or 0))
        return out

    # Plan B: strona /episodes bez tabeli z numerami — numerujemy po kolei.
    for i, href in enumerate(dict.fromkeys(RE_EP_HREF.findall(page)), 1):
        out.append(Episode(title=f"Odcinek {i}", ep_id=b64e(href), number=i))
    return out


class Shinden:
    """Klient Shindena: katalog + rozwiązywanie playerów do odtwarzalnego źródła."""

    def __init__(self, quality: int | None = None, min_interval: float = 0.6,
                 timeout: tuple[float, float] = (10.0, 40.0), retries: int = 3) -> None:
        self.quality = quality
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "pl-PL,pl;q=0.9"})

    # --- transport ------------------------------------------------------------

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _get(self, url: str, **kw) -> requests.Response:
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                last = e
                if attempt == self.retries:
                    raise ShindenError(f"GET {url}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
                continue
            return r
        raise ShindenError(f"GET {url}: {last}")

    def _page(self, url: str) -> str:
        r = self._get(url)
        if r.status_code != 200:
            raise ShindenError(f"{url}: HTTP {r.status_code}")
        return r.text

    # --- API ------------------------------------------------------------------

    def search(self, query: str) -> list[dict]:
        return parse_search(self._page(f"{BASE}/titles?search={requests.utils.quote(query)}"))

    def describe(self, ref: str) -> str:
        """Tytuł serii — z jej strony; w razie problemu z adresu."""
        try:
            page = self._page(BASE + ref)
            m = re.search(r"<title>(.*?)</title>", page, re.S)
            if m:
                return _clean(m.group(1).split("-")[0]) or ref
        except ShindenError:
            pass
        return ref.rsplit("/", 1)[-1].split("-", 1)[-1].replace("-", " ").strip().title()

    def episodes(self, ref: str) -> list[Episode]:
        path = ref if ref.startswith("/") else "/" + ref.split("shinden.pl", 1)[-1].lstrip("/")
        page = self._page(BASE + path.rstrip("/") + "/all-episodes")
        eps = parse_episodes(page)
        if not eps:
            eps = parse_episodes(self._page(BASE + path))
        if not eps:
            raise ShindenError(f"{path}: brak odcinków")
        return eps

    def player_iframe(self, page_url: str, online_id: str, auth: str) -> str:
        """Adres odtwarzacza dla jednej pozycji z listy playerów."""
        params = {"auth": auth}
        self._get(f"{XHR}/{online_id}/player_load", params=params,
                  headers={"X-Requested-With": "XMLHttpRequest", "Referer": page_url})
        body = ""
        deadline = time.monotonic() + PLAYER_WAIT + 6
        while time.monotonic() < deadline:
            time.sleep(PLAYER_WAIT)
            r = self._get(f"{XHR}/{online_id}/player_show",
                          params={**params, "width": 0, "height": -1},
                          headers={"X-Requested-With": "XMLHttpRequest", "Referer": page_url})
            body = r.text or ""
            m = RE_IFRAME.search(body)
            if m:
                src = html_mod.unescape(m.group(1))
                if src.startswith("//"):
                    src = "https:" + src
                return src
            if "Błędne wejście" in body:
                # Shinden nie zdążył — kolejna próba po pełnej przerwie
                continue
        return ""

    def links(self, ep_id: str) -> list[Link]:
        """Odcinek -> odtwarzalne źródła. Pusty wynik = brak obsługiwanego serwisu."""
        try:
            path = b64d(ep_id)
        except Exception as e:  # noqa: BLE001
            raise ShindenError(f"zły identyfikator odcinka: {ep_id}") from e

        page_url = BASE + path
        page = self._page(page_url)
        players = parse_players(page)
        if not players:
            raise ShindenError(f"{path}: brak playerów (usunięte albo wymaga logowania)")

        auth = parse_auth(page)
        hosts = sorted({str(p.get("player") or "?") for p in players})
        cda = [p for p in order_players(players) if str(p.get("player") or "").lower().startswith("cda")]
        client = Cda(quality=self.quality)

        for p in cda[:PLAYER_ATTEMPTS]:
            iframe = self.player_iframe(page_url, str(p["online_id"]), auth)
            vid = video_id_from_url(iframe)
            if not vid:
                continue
            try:
                links = client.links(vid)
            except CdaError:
                continue
            if links:
                return links

        if cda:
            raise ShindenError(f"{path}: playery CDA są, ale żaden nie oddał strumienia")
        raise ShindenError(
            f"{path}: brak obsługiwanego serwisu (dostępne: {', '.join(hosts)}) — "
            "obsługujemy tylko CDA"
        )
