"""Provider CDA.pl — drugie źródło, bez Cloudflare i bez logowania.

Ustalenia (2026-10, zweryfikowane na żywo):
  GET  /info?key=<fraza>            -> HTML, wyniki: /video/<id> oraz foldery
  GET  /<user>/folder/<id>          -> HTML, lista pozycji serwowana od razu
  GET  /video/<id>/vfilm            -> HTML z JSON-em odtwarzacza (manifest, qualities, ts, hash2)
  POST /video/<id>                  -> JSON-RPC videoGetLink => bezpośredni plik

CDA wydaje HLS `.m3u8` albo DASH `.mpd` (zależnie od filmu); `videoGetLink` zwraca
zwykły plik, co dla Jellyfina jest najlepsze (natywne przewijanie, bez remuxu).

Trwałe identyfikatory: odcinek to `cda-<video_id>` (prefiks rozdziela źródła
w jednym redirectorze), a serial to ścieżka folderu `/user/folder/123`.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import time

import requests

from oa import Episode, Link, ProviderError, UA
from urllib.parse import urljoin

BASE = "https://www.cda.pl"

# video_id w CDA bywa czysto liczbowe (2846605350), dlatego prefiks jest obowiązkowy.
EP_PREFIX = "cda-"

QUALITY_NAME = re.compile(r"^(\d{3,4})p$")

RE_VIDEO_HREF = re.compile(r'href="(/video/[0-9a-z]+)"', re.I)
RE_FOLDER_HREF = re.compile(r'href="(/[A-Za-z0-9_.-]+/folder/\d+)"')
RE_FOLDER_TITLE = re.compile(
    r'<span class="folder-title">([^<]{1,200})</span>\s*<span class="item-file">\(\s*(\d+)\s*\)</span>',
    re.S,
)
RE_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
RE_TITLE_ATTR = re.compile(r'title="([^"]*)"', re.I)
RE_HREF_ATTR = re.compile(r'href="([^"]*)"', re.I)

# numer odcinka w tytule: "odc. 12", "Odcinek 5", "(07)", "Super Mission 01"
RE_NUM_LABELLED = re.compile(r"(?:odc\.?|odcinek|ep\.?|episode|e)\s*(\d{1,4})", re.I)
RE_NUM_TRAILING = re.compile(r"(\d{1,4})\s*$")
RE_NUM_PAREN = re.compile(r"\(\s*(\d{1,4})\s*\)")


class CdaError(ProviderError):
    pass


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(value or "")).strip()


def _attr(attrs: str, name: str) -> str:
    m = re.search(rf'{name}="([^"]*)"', attrs, re.I)
    return m.group(1) if m else ""


def _strip_tags(fragment: str) -> str:
    return _clean(re.sub(r"<[^>]+>", " ", fragment))


def episode_number(title: str) -> int | None:
    """Numer odcinka z tytułu pozycji w folderze."""
    for rx in (RE_NUM_LABELLED, RE_NUM_PAREN):
        m = rx.search(title)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if 0 < n <= 3000:
                return n

    m = RE_NUM_TRAILING.search(title)
    if m:
        try:
            n = int(m.group(1))
            if 0 < n <= 3000 and not (1900 <= n <= 2099):
                return n
        except ValueError:
            pass

    return None


def player_config(page: str) -> dict:
    """Wyciąga JSON odtwarzacza z HTML-a strony /video/<id>/vfilm.

    Obiekt zawiera m.in. manifest, qualities, ts, hash2. Wycinamy go skanerem
    nawiasów (świadomym łańcuchów i escapowania), bo wyrażenie regularne
    przewraca się na zagnieżdżonych obiektach — a gdyby się nie udało,
    zbieramy pola pojedynczo jako plan B.
    """
    idx = page.find('"manifest"')
    if idx != -1:
        start = page.rfind("{", 0, idx)
        if start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(page)):
                ch = page[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(page[start : i + 1])
                        except json.JSONDecodeError:
                            break

    cfg: dict = {}
    for key, rx in (
        ("manifest", r'"manifest":"([^"]+)"'),
        ("ts", r'"ts":(\d+)'),
        ("hash2", r'"hash2":"([^"]+)"'),
        ("premium", r'"premium":(true|false)'),
        ("title", r'"title":"([^"]*)"'),
        ("duration", r'"durationFull":"([^"]*)"'),
    ):
        m = re.search(rx, page)
        if m:
            cfg[key] = m.group(1)
    qm = re.search(r'"qualities":\{([^}]*)\}', page)
    if qm:
        cfg["qualities"] = {
            k: v for k, v in re.findall(r'"([^"]+)":"([^"]*)"', qm.group(1))
        }
    # Manifest bywa `null` (materiały, dla których CDA wydaje tylko link na żądanie).
    # Wtedy liczy się to, czym podpisuje się videoGetLink: ts + hash2.
    if not cfg.get("manifest") and not (cfg.get("ts") and cfg.get("hash2")):
        raise CdaError("nie znaleziono danych odtwarzacza na stronie odcinka")
    return cfg


def _poster(chunk: str) -> str:
    m = re.search(r'<img[^>]+class="video-clip-image"[^>]+src="([^"]+)"', chunk)
    if not m:
        m = re.search(r'<img[^>]+src="([^"]+)"[^>]+class="video-clip-image"', chunk)
    if not m:
        return ""
    src = m.group(1)
    return "https:" + src if src.startswith("//") else src


def parse_search(page: str) -> list[dict]:
    """Wyniki wyszukiwania: filmy i foldery.

    Tytuł bierzemy z odnośnika `link-title-visit` — to on niesie nazwę widoczną
    dla człowieka. Atrybut `title` na karcie bywa długością materiału („02:13:19")
    albo licznikiem plików („8 plików"), więc nie nadaje się na nazwę serialu.
    """
    items: list[dict] = []
    seen: set[str] = set()

    for chunk in page.split('<div class="video-clip-wrapper"')[1:]:
        chunk = chunk[:5000]
        m = re.search(
            r'<a class="link-title-visit"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', chunk, re.S
        )
        if not m:
            continue
        href, title = m.group(1), _strip_tags(m.group(2))
        if not title:
            continue
        vid = re.fullmatch(r"/video/([0-9a-z]+)", href)
        if vid:
            ref, kind = EP_PREFIX + vid.group(1), "video"
        elif re.fullmatch(r"/[A-Za-z0-9_.-]+/folder/\d+", href):
            ref, kind = href, "folder"
        else:
            continue
        if ref in seen:
            continue
        seen.add(ref)
        items.append({"ref": ref, "title": title, "kind": kind, "poster": _poster(chunk)})

    if items:
        return items

    # Plan B: starszy układ kart (bez `video-clip-wrapper`)
    for m in RE_ANCHOR.finditer(page):
        attrs, inner = m.group(1), m.group(2)
        href = _attr(attrs, "href")
        vid = re.fullmatch(r"/video/([0-9a-z]+)", href or "")
        fol = re.fullmatch(r"/[A-Za-z0-9_.-]+/folder/\d+", href or "")
        if not (vid or fol):
            continue
        title = _clean(_attr(attrs, "title")) or _strip_tags(inner)
        ref = EP_PREFIX + vid.group(1) if vid else href
        if title and ref not in seen:
            seen.add(ref)
            items.append(
                {"ref": ref, "title": title, "kind": "video" if vid else "folder", "poster": ""}
            )
    return items


def parse_folder(page: str) -> dict:
    """Zawartość folderu: tytuł, filmy (w kolejności uploadera) i podfoldery."""
    title = ""
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if m:
        title = _clean(m.group(1)).split(" - ")[0]

    videos: list[dict] = []
    seen: set[str] = set()
    for m in RE_ANCHOR.finditer(page):
        attrs, inner = m.group(1), m.group(2)
        href = _attr(attrs, "href")
        vid = re.fullmatch(r"/video/([0-9a-z]+)", href or "")
        if not vid or vid.group(1) in seen:
            continue
        t = _clean(_attr(attrs, "title")) or _strip_tags(inner)
        if not t:
            continue
        seen.add(vid.group(1))
        videos.append({"id": vid.group(1), "title": t})

    subfolders = [
        {"title": _clean(t), "count": int(c), "ref": None}
        for t, c in RE_FOLDER_TITLE.findall(page)
    ]
    # podfolderom dopisujemy adresy z drzewa katalogów (kolejność jest zgodna)
    hrefs = [h for h in dict.fromkeys(RE_FOLDER_HREF.findall(page))]
    for sf, href in zip(subfolders, hrefs):
        sf["ref"] = href

    return {"title": title, "videos": videos, "subfolders": subfolders}


def build_episodes(videos: list[dict]) -> list[Episode]:
    """Filmy folderu -> odcinki. Bez numerów w tytułach numerujemy po kolei."""
    numbered = [(v, episode_number(v["title"])) for v in videos]
    parsed = [n for _, n in numbered if n is not None]
    if len(parsed) < max(1, len(videos) // 2):
        numbered = [(v, i) for i, v in enumerate(videos, 1)]

    out: list[Episode] = []
    used: set[int] = set()
    special_no = 0
    for v, num in numbered:
        if num is not None and num not in used:
            used.add(num)
            out.append(Episode(title=v["title"], ep_id=v["id"], number=num))
        else:
            special_no += 1
            out.append(
                Episode(
                    title=v["title"] or f"Materiał dodatkowy {special_no}",
                    ep_id=v["id"],
                    number=special_no,
                    season=0,
                )
            )
    return out


def dedupe_variants(links: list[Link]) -> list[Link]:
    """Zostawia po jednym wariancie na wysokość, najwyższe pierwsze.

    Ścieżki audio (`a_*`, bez wysokości) są odrzucane, gdy jest choć jedno wideo —
    dla odcinka serialu liczy się obraz, a taki wariant zepsułby wybór najlepszej jakości.
    """
    video = [l for l in links if l.res > 0]
    if not video:
        return sorted(links, key=lambda l: l.res, reverse=True)

    best: dict[int, Link] = {}
    for link in video:
        best.setdefault(link.res, link)
    return [best[res] for res in sorted(best, reverse=True)]


def video_id_from_url(value: str) -> str | None:
    """Wyciąga identyfikator filmu z dowolnego adresu CDA.

    Shinden linkuje odtwarzacz przez `ebd.cda.pl/<szer>x<wys>/<id>`, nasze
    własne strony używają `/video/<id>`. Oba prowadzą do tego samego filmu.
    """
    v = (value or "").strip()
    if not v:
        return None
    m = re.search(r"/video/([0-9a-z]+)", v, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"ebd\.cda\.pl/\d+x\d+/([0-9a-z]+)", v, re.I)
    if m:
        return m.group(1).lower()
    m = re.fullmatch(r"([0-9a-z]{6,})", v, re.I)
    return m.group(1).lower() if m else None


def normalize_ref(value: str) -> str | None:
    """Zamienia wklejony link CDA na identyfikator zrozumiały dla providera.

    Obsługuje pełny URL i samą ścieżkę, folder oraz pojedynczy film.
    """
    v = (value or "").strip()
    if not v:
        return None
    m = re.search(r"(/[A-Za-z0-9_.-]+/folder/\d+)", v)
    if m:
        return m.group(1)
    vid = video_id_from_url(v)
    return EP_PREFIX + vid if vid else None


class Cda:
    """Klient CDA: wyszukiwanie, foldery jako seriale, linki do odcinków."""

    def __init__(self, quality: int | None = None, min_interval: float = 0.7,
                 timeout: tuple[float, float] = (10.0, 45.0), retries: int = 3) -> None:
        self.quality = quality
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "pl-PL,pl;q=0.9"})
        # bez tego cookie CDA podaje odtwarzacz flash zamiast html5
        self.s.cookies.set("cda.player", "html5", domain="www.cda.pl")

    # --- transport ------------------------------------------------------------

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("headers", {})
        referer = kw.pop("referer", BASE + "/")
        kw["headers"].setdefault("Referer", referer)
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle()
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                last = e
                if attempt == self.retries:
                    raise CdaError(f"{method} {url}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
                continue
            return r
        raise CdaError(f"{method} {url}: {last}")

    def _page(self, url: str, referer: str | None = None) -> str:
        r = self._request("GET", url, referer=referer) if referer else self._request("GET", url)
        if r.status_code == 404:
            raise CdaError(f"{url}: 404")
        if r.status_code != 200:
            raise CdaError(f"{url}: HTTP {r.status_code}")
        return r.text

    # --- API ------------------------------------------------------------------

    def search(self, query: str) -> list[dict]:
        page = self._page(f"{BASE}/info?key={requests.utils.quote(query)}")
        return parse_search(page)

    def folder(self, ref: str) -> dict:
        """Zawartość folderu. `ref` to ścieżka (/user/folder/123) albo pełny URL."""
        path = ref if ref.startswith("/") else "/" + ref.split("cda.pl/", 1)[-1].lstrip("/")
        page = self._page(BASE + path, referer=BASE + "/")
        data = parse_folder(page)
        if not data["videos"] and not data["subfolders"]:
            raise CdaError(f"folder {path}: brak pozycji")
        return data

    def episodes(self, ref: str) -> list[Episode]:
        """Serial = folder. Dla pojedynczego filmu zwraca jeden odcinek."""
        vid = re.fullmatch(r"cda-([0-9a-z]+)", ref) or re.fullmatch(r"/video/([0-9a-z]+)", ref)
        if vid:
            cfg = self.video(vid.group(1))
            title = cfg.get("title") or vid.group(1)
            return [Episode(title=_clean(requests.utils.unquote(title)) or vid.group(1),
                            ep_id=vid.group(1), number=1)]
        data = self.folder(ref)
        if not data["videos"]:
            names = ", ".join(f"{s['title']} ({s['ref']})" for s in data["subfolders"][:5])
            raise CdaError(
                "folder nie zawiera filmów, tylko podfoldery — wybierz jeden z nich: " + names
            )
        return build_episodes(data["videos"])

    def describe(self, ref: str) -> str:
        """Tytuł serialu/filmu — do nazwy katalogu w bibliotece."""
        vid = re.fullmatch(r"cda-([0-9a-z]+)", ref)
        if vid:
            cfg = self.video(vid.group(1))
            return _clean(requests.utils.unquote(cfg.get("title") or "")) or vid.group(1)
        return self.folder(ref)["title"]

    def video(self, vid: str) -> dict:
        """Konfiguracja odtwarzacza dla filmu (manifest, qualities, ts, hash2)."""
        page = self._page(f"{BASE}/video/{vid}/vfilm", referer=BASE + "/")
        return player_config(page)

    def _variants(self, mpd_url: str) -> list[Link]:
        """Rozbiera manifest DASH na pojedyncze pliki.

        CDA używa profilu on-demand: `<BaseURL>` wskazuje jeden plik MP4
        z indeksem (SegmentBase indexRange), a manifest opisuje dostępne jakości.
        Podanie takiego pliku wprost jest dla Jellyfina lepsze niż DASH —
        odtwarza natywnie i pozwala przewijać.
        """
        try:
            xml = self._page(mpd_url)
        except CdaError:
            return []

        reps = re.findall(r"<Representation\b([^>]*)>(.*?)</Representation>", xml, re.S)
        if not reps:
            reps = [(a, "") for a in re.findall(r"<Representation\b([^>]*)/>", xml)]

        out: list[Link] = []
        for attrs, body in reps:
            h = _attr(attrs, "height")
            bw = _attr(attrs, "bandwidth")
            m = re.search(r"<BaseURL>([^<]+)</BaseURL>", body)
            if not m:
                continue
            src = urljoin(mpd_url, _clean(m.group(1)))
            res = int(h) if h.isdigit() else 0
            out.append(
                Link(
                    label=f"{res}p" if res else f"{bw}bps",
                    res=res,
                    src=src,
                    main_url=mpd_url,
                    mime="video/mp4",
                )
            )
        return out

    def links(self, vid: str) -> list[Link]:
        """Pliki odcinka, najlepsza jakość pierwsza. Pusty wynik = brak źródeł.

        Pytamy o jakości od najwyższej. Manifest, który zwraca `videoGetLink`,
        rozbieramy na pojedyncze pliki — każda odpowiedź opisuje zwykle wszystkie
        dostępne jakości, dlatego przerywamy po pierwszej udanej i deduplikujemy
        po wysokości (inaczej jedna jakość wracałaby kilka razy).
        """
        cfg = self.video(vid)
        quals = {k: v for k, v in (cfg.get("qualities") or {}).items() if QUALITY_NAME.match(k)}
        ordered = sorted(quals.items(), key=lambda kv: -int(QUALITY_NAME.match(kv[0]).group(1)))

        out: list[Link] = []
        for name, code in ordered:
            url = self._get_link(vid, code, cfg)
            if not url:
                continue
            res = int(QUALITY_NAME.match(name).group(1))

            # `.mpd` rozbieramy na pojedyncze pliki (profil on-demand).
            # `.m3u8` i `.mp4` są gotowe do podania — zwłaszcza HLS z CDN,
            # który przechodzi u każdego klienta bez nagłówków i ciasteczek.
            if ".mpd" in url:
                got = self._variants(url)
                if got:
                    out.extend(got)
                    break
                mime = "application/dash+xml"
            else:
                mime = "application/x-mpegURL" if ".m3u8" in url else "video/mp4"
            out.append(Link(label=name, res=res, src=url, main_url=url, mime=mime))
            if self.quality and res == self.quality:
                break

        # manifest jako plan B (HLS/DASH) — gdy videoGetLink odmówi
        if not out and cfg.get("manifest"):
            man = cfg["manifest"]
            mime = "application/x-mpegURL" if ".m3u8" in man else "application/dash+xml"
            return [Link(label="manifest", res=0, src=man, main_url=man, mime=mime)]

        return dedupe_variants(out)

    def _get_link(self, vid: str, code: str, cfg: dict) -> str | None:
        payload = {
            "jsonrpc": "2.0",
            "method": "videoGetLink",
            "id": 2,
            "params": [vid, code, int(cfg.get("ts") or 0), cfg.get("hash2") or "", {}],
        }
        try:
            r = self._request(
                "POST",
                f"{BASE}/video/{vid}",
                referer=f"{BASE}/video/{vid}/vfilm",
                headers={
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                data=json.dumps(payload),
            )
        except CdaError:
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except json.JSONDecodeError:
            return None
        result = data.get("result") or {}
        if result.get("status") != "ok":
            return None
        url = result.get("resp")
        return str(url) if url else None
