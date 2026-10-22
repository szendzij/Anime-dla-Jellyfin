"""Klient API ogladajanime.pl — czysty, bez regexów, z trwałą sesją.

Powstał z analizy k0aziu/ogladajanime-cli: mapa endpointów jest jego, implementacja nowa.
Zmiany względem oryginału:
  * sesja w cookie-jarze na dysku (brak logowania przy każdym starcie, 0600),
  * parser HTML na HTMLParser zamiast regexa po atrybutach,
  * numer odcinka z atrybutu `value`, nie z pozycji w dokumencie,
  * timeouty, retry z backoffem, rate limit,
  * własne wyjątki zamiast wywalań na dowolnym stringu.

Endpointy (ustalone empirycznie 2026-10):
  GET  /                                              -> seed PHPSESSID
  POST /manager.php?action=login   {login, pass}       -> {"data":"OK"} + user_id, user_key, cf_clear
  GET  /manager.php?action=get_anime_names             -> {"json":[...]}
  POST /manager.php?action=get_search {page,search_type,search} -> {"data": "<html>"}
  GET  /anime/<slug>                                   -> HTML, <li ... title= ep_id= value=>
  GET  :8443/Player/<ep_id>                            -> [{label,res,src,mainUrl,...}] lub 404
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Iterable

import requests

BASE = "https://ogladajanime.pl"
PLAYER = "https://ogladajanime.pl:8443/Player/"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

DEFAULT_SESSION_FILE = Path(
    os.environ.get("OA_SESSION_FILE", "~/.config/ogladajanime/session.lwp")
).expanduser()


try:
    from providers import ProviderError
except (ImportError, ModuleNotFoundError):
    class ProviderError(RuntimeError):
        pass


class OgladajAnimeError(ProviderError):
    pass


class AuthError(OgladajAnimeError):
    pass


@dataclass(frozen=True)
class Episode:
    title: str
    ep_id: str
    number: int | None  # None = materiał dodatkowy (trailer/special)
    season: int = 1

    @property
    def kind(self) -> str:
        return "episode" if self.number is not None else "special"


@dataclass(frozen=True)
class Link:
    label: str
    res: int
    src: str
    main_url: str | None = None
    mime: str = ""


class _EpisodeParser(HTMLParser):
    """Czyta <li> z klasą list-group-item i wyciąga atrybuty jednego tagu.

    Odporne na kolejność atrybutów — regex z oryginału wymagał
    class -> title -> ep_id i pękał przy każdej zmianie szablonu.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "li":
            return
        a = {k: (v or "") for k, v in attrs}
        if "list-group-item" not in a.get("class", ""):
            return
        if "ep_id" not in a:
            return
        self.raw.append(a)


class _SearchParser(HTMLParser):
    """Wyciąga pozycje z HTML zwracanego przez get_search.

    Każdy wynik to karta z <a href="/anime/<slug>"> i aria-label = tytuł, oraz <img>
    z okładką. Deduplikujemy po slugu (ten sam link występuje w karcie dwa razy).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: dict[str, dict] = {}
        self._cur: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "a":
            m = re.fullmatch(r"/anime/([A-Za-z0-9_-]+)", a.get("href", ""))
            if not m:
                return
            item = self.items.setdefault(
                m.group(1), {"slug": m.group(1), "title": "", "poster": "", "anime_id": None}
            )
            if a.get("aria-label"):
                item["title"] = a["aria-label"]
            idm = re.search(r"loadAnimePage\((\d+)", a.get("onclick", ""))
            if idm:
                item["anime_id"] = idm.group(1)
            self._cur = m.group(1)
        elif tag == "img" and self._cur:
            item = self.items[self._cur]
            src = a.get("data-srcset") or a.get("src") or ""
            if src and not item["poster"]:
                item["poster"] = src.split(" ")[0]


def parse_search(page_html: str) -> list[dict]:
    p = _SearchParser()
    p.feed(page_html)
    return [i for i in p.items.values() if i["title"]]


def parse_episodes(page_html: str) -> list[Episode]:
    """Zamienia HTML strony anime na listę odcinków.

    `value` to numer odcinka nadawany przez serwis: 1-based dla odcinków,
    0 dla zapowiedzi/trailerów. Gdy numeru brak, jest zerowy albo się powtarza,
    materiał trafia do sezonu 0 (Specials) — inaczej Jellyfin zobaczyłby dwa
    odcinki o tym samym SxxExx.
    """
    p = _EpisodeParser()
    p.feed(page_html)

    used: set[int] = set()
    special_no = 0
    out: list[Episode] = []

    for a in p.raw:
        title = html_mod.unescape(a.get("title", "")).strip()
        raw_value = a.get("value", "").strip()
        ep_id = a["ep_id"]

        number: int | None = None
        if raw_value.isdigit():
            candidate = int(raw_value)
            if candidate > 0 and candidate not in used:
                used.add(candidate)
                number = candidate

        if number is None:
            special_no += 1
            out.append(
                Episode(
                    title=title or f"Materiał dodatkowy {special_no}",
                    ep_id=ep_id,
                    number=special_no,
                    season=0,
                )
            )
        else:
            out.append(Episode(title=title or f"Odcinek {number}", ep_id=ep_id, number=number))

    return out


class OgladajAnime:
    def __init__(
        self,
        session_file: Path = DEFAULT_SESSION_FILE,
        min_interval: float = 1.0,
        timeout: tuple[float, float] = (10.0, 45.0),
        retries: int = 3,
    ) -> None:
        self.session_file = Path(session_file)
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self._load_cookies()

    # --- warstwa transportowa -------------------------------------------------

    def _load_cookies(self) -> None:
        if not self.session_file.exists():
            return
        try:
            jar = MozillaCookieJar(str(self.session_file))
            jar.load(ignore_discard=True, ignore_expires=True)
            self.s.cookies.update(jar)
        except (OSError, requests.cookies.CookieConflictError):
            pass

    def save_session(self) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        jar = MozillaCookieJar(str(self.session_file))
        for c in self.s.cookies:
            jar.set_cookie(c)
        jar.save(ignore_discard=True, ignore_expires=True)
        self.session_file.chmod(0o600)

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        headers.setdefault("X-Requested-With", "XMLHttpRequest")
        headers.setdefault("Origin", BASE)
        headers.setdefault("Referer", kw.pop("referer", BASE + "/"))

        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                r = self.s.request(method, url, headers=headers, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                last = e
                if attempt == self.retries:
                    raise OgladajAnimeError(f"{method} {url}: {e}") from e
                time.sleep(2 ** attempt)
                continue

            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                wait = float(r.headers.get("Retry-After") or 2 ** attempt)
                time.sleep(min(wait, 30))
                continue
            return r
        raise OgladajAnimeError(f"{method} {url}: {last}")

    # --- API ------------------------------------------------------------------

    def login(self, login: str, password: str) -> None:
        self._request("GET", BASE + "/", headers={"Referer": BASE + "/"})
        r = self._request(
            "POST",
            BASE + "/manager.php?action=login",
            data={"login": login, "pass": password},
        )
        if r.status_code != 200:
            raise AuthError(f"Login HTTP {r.status_code}")
        try:
            payload = r.json()
        except json.JSONDecodeError as e:
            raise AuthError(f"Login: odpowiedź nie jest JSON ({r.text[:120]!r})") from e
        if str(payload.get("data", "")).upper() != "OK":
            raise AuthError(f"Login odrzucony: {payload.get('data')!r}")
        self.save_session()

    def is_logged_in(self) -> bool:
        r = self._request("GET", BASE + "/", headers={"Referer": BASE + "/"})
        return "Wyloguj" in r.text

    def ensure_login(self) -> None:
        try:
            if self.is_logged_in():
                return
        except OgladajAnimeError:
            pass
        login = os.environ.get("OA_LOGIN")
        password = os.environ.get("OA_PASS")
        if not login:
            # W kontenerze nie ma z kim rozmawiać — pytanie o login zawiesiłoby
            # usługę na zawsze. Brak danych to błąd, nie zaproszenie do rozmowy.
            import sys

            if not sys.stdin.isatty():
                raise AuthError(
                    "brak poświadczeń ogladajanime (OA_LOGIN / OA_PASS) w trybie nieinteraktywnym"
                )
            import getpass

            login = input("Login ogladajanime: ").strip()
            password = getpass.getpass("Hasło: ")
        if not password:
            raise AuthError("Brak hasła (ustaw OA_PASS albo uruchom interaktywnie).")
        self.login(login, password)

    def anime_names(self) -> list[str]:
        r = self._request("GET", BASE + "/manager.php?action=get_anime_names")
        if r.status_code != 200:
            raise AuthError(f"get_anime_names HTTP {r.status_code} — sesja wygasła?")
        data = r.json().get("json")
        if not isinstance(data, list):
            raise OgladajAnimeError("get_anime_names: oczekiwano tablicy w polu 'json'")
        return [str(x) for x in data]

    def search(self, query: str, page: int = 1) -> list[str]:
        r = self._request(
            "POST",
            BASE + "/manager.php?action=get_search",
            data={"page": page, "search_type": "name", "search": query},
        )
        blob = r.json().get("data") or ""
        return [i["slug"] for i in parse_search(blob)]

    def search_results(self, query: str, page: int = 1) -> list[dict]:
        """Wyniki wyszukiwania z tytułem, okładką i anime_id."""
        r = self._request(
            "POST",
            BASE + "/manager.php?action=get_search",
            data={"page": page, "search_type": "name", "search": query},
        )
        items = parse_search(r.json().get("data") or "")
        q = query.strip().lower()

        def rank(i: dict) -> tuple:
            t = i["title"].lower()
            return (0 if t == q else 1 if t.startswith(q) else 2, len(t))

        return sorted(items, key=rank)

    def episodes(self, slug: str) -> list[Episode]:
        r = self._request("GET", f"{BASE}/anime/{slug}")
        if r.status_code != 200:
            raise OgladajAnimeError(f"/anime/{slug} HTTP {r.status_code}")
        return parse_episodes(r.text)

    def links(self, ep_id: str, slug: str | None = None) -> list[Link]:
        referer = f"{BASE}/anime/{slug}" if slug else BASE + "/"
        r = self._request("GET", PLAYER + str(ep_id), referer=referer)
        if r.status_code == 404:
            return []  # odcinek bez dostępnych źródeł — normalne dla starszych serii
        if r.status_code != 200:
            raise OgladajAnimeError(f"Player {ep_id} HTTP {r.status_code}")
        try:
            items = r.json()
        except json.JSONDecodeError as e:
            raise OgladajAnimeError(f"Player {ep_id}: nie-JSON ({r.text[:100]!r})") from e
        if not isinstance(items, list):
            raise OgladajAnimeError(f"Player {ep_id}: oczekiwano tablicy")
        out = [
            Link(
                label=str(i.get("label") or f"{i.get('res', '?')}p"),
                res=int(i.get("res") or 0),
                src=str(i.get("src") or ""),
                main_url=i.get("mainUrl"),
                mime=str(i.get("type") or ""),
            )
            for i in items
            if i.get("src")
        ]
        out.sort(key=lambda l: l.res, reverse=True)
        return out


def pick_best(links: Iterable[Link], preferred: int | None = None) -> Link | None:
    links = list(links)
    if not links:
        return None
    if preferred:
        for l in links:
            if l.res == preferred:
                return l
        return None
    return links[0]
