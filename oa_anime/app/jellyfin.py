"""Nazewnictwo zgodne z Jellyfin + zapis .strm i pobieranie.

Konwencja: <Serial>/Season 01/<Serial> - S01E01 - <tytuł>.strm
Materiały dodatkowe (trailery, duplikaty numerów): <Serial>/Specials/<Serial> - S00E01 - <tytuł>.strm
"""

from __future__ import annotations

import re
import shutil
import time
import subprocess
import unicodedata
from pathlib import Path

import requests

from oa import UA as _BROWSER_UA
from oa import Episode, Link

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
MAX_TITLE = 120


def sanitize(name: str) -> str:
    """Usuwa znaki niedozwolone w nazwach plików i skraca do rozsądnej długości."""
    name = unicodedata.normalize("NFC", name)
    name = _ILLEGAL.sub(" ", name)
    name = _WS.sub(" ", name).strip(" .")
    if len(name) > MAX_TITLE:
        name = name[:MAX_TITLE].rsplit(" ", 1)[0]
    return name or "Bez tytułu"


def season_dir(ep: Episode) -> str:
    return "Specials" if ep.season == 0 else f"Season {ep.season:02d}"


def episode_filename(series: str, ep: Episode) -> str:
    code = f"S{ep.season:02d}E{ep.number:02d}"
    return f"{sanitize(series)} - {code} - {sanitize(ep.title)}.strm"


def target_path(root: Path, series: str, ep: Episode) -> Path:
    return Path(root) / sanitize(series) / season_dir(ep) / episode_filename(series, ep)


def write_strm_url(root: Path, series: str, ep: Episode, url: str) -> Path:
    path = target_path(root, series, ep)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url + "\n", encoding="utf-8")
    return path


def write_strm(root: Path, series: str, ep: Episode, link: Link) -> Path:
    return write_strm_url(root, series, ep, link.src)


def _resolve(base_url: str, ref: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base_url, ref)


def _hls_download(
    src: str, final: Path, headers: dict[str, str], max_segments: int | None = None
) -> Path:
    """Pobiera strumień HLS bez ffmpeg: master -> wariant -> segmenty -> jeden plik.

    Obsługuje playlisty z segmentami MPEG-TS i fMP4 (#EXT-X-MAP).
    Szyfrowania AES-128 nie obsługuje — zgłasza to jasno zamiast zapisać śmieci.
    """
    with requests.get(src, headers=headers, timeout=(10, 60)) as r:
        r.raise_for_status()
        playlist = r.text
        playlist_url = r.url

    if "#EXT-X-KEY:METHOD=AES-128" in playlist:
        raise RuntimeError("Strumień szyfrowany AES-128 — potrzebny ffmpeg lub yt-dlp")

    # master playlist -> wybierz wariant o najwyższym BANDWIDTH
    variants = [
        (int(bw), _resolve(playlist_url, uri.strip()))
        for bw, uri in re.findall(
            r"#EXT-X-STREAM-INF:[^\n]*?BANDWIDTH=(\d+)[^\n]*\n([^\n#]+)", playlist
        )
    ]
    if variants:
        _, variant_url = max(variants)
        with requests.get(variant_url, headers=headers, timeout=(10, 60)) as r:
            r.raise_for_status()
            playlist = r.text
            playlist_url = r.url

    init_seg = None
    m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist)
    if m:
        init_seg = _resolve(playlist_url, m.group(1))

    segments = [
        _resolve(playlist_url, line.strip())
        for line in playlist.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not segments:
        raise RuntimeError("Playlista HLS nie zawiera segmentów")
    if max_segments:
        segments = segments[:max_segments]

    tmp = final.with_suffix(final.suffix + ".part")
    total = 0
    with tmp.open("wb") as fh:
        if init_seg:
            with requests.get(init_seg, headers=headers, timeout=(10, 60)) as r:
                r.raise_for_status()
                fh.write(r.content)
                total += len(r.content)
        for i, seg in enumerate(segments, 1):
            for attempt in (1, 2, 3):
                try:
                    with requests.get(seg, headers=headers, timeout=(10, 60)) as r:
                        r.raise_for_status()
                        data = r.content
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(attempt)
            fh.write(data)
            total += len(data)
            if i % 25 == 0 or i == len(segments):
                print(f"     ..{i}/{len(segments)} segmentów, {total / 2**20:.1f} MiB")
    if total == 0:
        raise RuntimeError("Pobrano 0 bajtów")
    tmp.replace(final)
    return final


def download_media(
    dest: Path,
    link: Link,
    use_ytdlp: bool | None = None,
    max_segments: int | None = None,
) -> Path:
    """Pobiera materiał do `dest` (bez rozszerzenia — dobierane z kontenera).

    yt-dlp, jeśli jest w PATH (najprostsza droga, radzi sobie z każdym playerem).
    Inaczej: HLS składany ręcznie z segmentów (bez ffmpeg) albo strumień bezpośredni.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if use_ytdlp is None:
        use_ytdlp = shutil.which("yt-dlp") is not None

    if use_ytdlp:
        out = dest.with_suffix(".%(ext)s")
        cmd = ["yt-dlp", "--no-playlist", "--continue", "-o", str(out), link.src]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp kod {proc.returncode}: {proc.stderr.strip()[-400:]}")
        produced = sorted(dest.parent.glob(dest.name + ".*"), key=lambda p: p.stat().st_mtime)
        if not produced:
            raise RuntimeError("yt-dlp zakończył się sukcesem, ale nie zapisał pliku")
        return produced[-1]

    headers = {"User-Agent": _BROWSER_UA, "Referer": "https://ogladajanime.pl/"}
    if ".m3u8" in link.src:
        return _hls_download(link.src, dest.with_suffix(".ts"), headers, max_segments)

    final = dest.with_suffix(".mp4")
    tmp = final.with_suffix(final.suffix + ".part")
    req_headers = dict(headers)
    if tmp.exists():
        req_headers["Range"] = f"bytes={tmp.stat().st_size}-"

    with requests.get(link.src, stream=True, timeout=(10, 120), headers=req_headers) as r:
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HTTP {r.status_code} przy pobieraniu")
        mode = "ab" if r.status_code == 206 and tmp.exists() else "wb"
        with tmp.open(mode) as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
    tmp.replace(final)
    return final
