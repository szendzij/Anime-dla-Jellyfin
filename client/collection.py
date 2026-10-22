"""Logika kolekcji: dodawanie seriali, synchronizacja, skan Jellyfina.

Wspólna warstwa dla panelu (kolekcja.py) i crona (sync.py). Źródło jest teraz
parametrem: wpisy pamiętają, skąd pochodzą, a stare (bez pola `source`)
traktujemy jako ogladajanime, żeby istniejąca kolekcja nie wymagała migracji.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import requests

from jellyfin import sanitize, write_strm_url
from providers import OA, Episode, Source

DEFAULT_SOURCE = OA


def load(path: Path) -> dict:
    if not path.exists():
        return {"series": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"series": {}}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def entry_source(entry: dict) -> str:
    return entry.get("source") or DEFAULT_SOURCE


def entry_ref(key: str, entry: dict) -> str:
    """Identyfikator serialu w źródle. Starsze wpisy mają go tylko w kluczu."""
    return entry.get("ref") or key


def _series_dir(root: Path, title: str) -> Path:
    return Path(root) / sanitize(title)


def add_series(
    source: Source,
    data: dict,
    ref: str,
    title: str,
    root: Path,
    base_url: str,
    poster: str = "",
    key: str | None = None,
) -> dict:
    """Generuje .strm dla wszystkich odcinków i dopisuje serial do kolekcji."""
    if not base_url:
        raise RuntimeError(
            "Brak adresu redirectora (base_url). Bez niego .strm wygaśnie po kilku godzinach."
        )

    episodes = source.episodes(ref)
    if not episodes:
        raise RuntimeError(f"'{ref}' nie ma odcinków")

    written = 0
    for ep in episodes:
        write_strm_url(Path(root), title, ep, source.strm_url(base_url, ep.ep_id))
        written += 1

    main = [e for e in episodes if e.season == 1]
    data["series"][key or ref] = {
        "source": source.name,
        "ref": ref,
        "title": title,
        "poster": poster,
        "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "root": str(root),
        "base_url": base_url,
        "episodes": len(main),
        "files": written,
    }
    return data["series"][key or ref]


def remove_series(data: dict, key: str) -> bool:
    entry = data["series"].pop(key, None)
    if not entry:
        return False
    folder = _series_dir(Path(entry["root"]), entry["title"])
    root = Path(entry["root"]).resolve()
    try:
        if folder.exists() and root in folder.resolve().parents:
            shutil.rmtree(folder)
    except OSError:
        pass
    return True


def sync_series(source: Source, key: str, entry: dict) -> dict:
    """Dopisuje .strm dla odcinków, których jeszcze nie ma na dysku."""
    root = Path(entry["root"])
    base_url = entry["base_url"]
    title = entry["title"]
    overrides = entry.get("player_overrides", {})

    episodes = source.episodes(entry_ref(key, entry))
    added = 0
    for ep in episodes:
        if not source.prefix and not str(ep.ep_id).isdigit():
            continue
        path = _target(root, title, ep)
        if not path.exists():
            pidx = overrides.get(str(ep.ep_id))
            write_strm_url(root, title, ep, source.strm_url(base_url, ep.ep_id, player_idx=pidx))
            added += 1

    entry["episodes"] = len([e for e in episodes if e.season == 1])
    entry["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {"key": key, "title": title, "added": added, "total": len(episodes)}


def set_episode_player(
    data: dict,
    key: str,
    ep_id: str,
    player_idx: int | None,
    source: Source,
) -> str:
    """Zmienia wybrany odtwarzacz dla danego odcinka i aktualizuje plik .strm."""
    entry = data.get("series", {}).get(key)
    if not entry:
        raise KeyError(f"Nie znaleziono serii '{key}' w kolekcji")

    overrides = entry.setdefault("player_overrides", {})
    ep_id_str = str(ep_id)
    if player_idx is not None and player_idx >= 0:
        overrides[ep_id_str] = int(player_idx)
    else:
        overrides.pop(ep_id_str, None)

    ref = entry_ref(key, entry)
    episodes = source.episodes(ref)
    target_ep = next((e for e in episodes if str(e.ep_id) == ep_id_str), None)
    if not target_ep:
        raise ValueError(f"Nie znaleziono odcinka o id '{ep_id}' w serii '{key}'")

    url = source.strm_url(
        entry["base_url"],
        target_ep.ep_id,
        player_idx=player_idx if player_idx is not None and player_idx >= 0 else None,
    )
    write_strm_url(Path(entry["root"]), entry["title"], target_ep, url)
    return url


def _target(root: Path, title: str, ep: Episode) -> Path:
    from jellyfin import target_path

    return target_path(root, title, ep)


def trigger_scan_status(url: str | None = None, key: str | None = None) -> tuple[bool, str]:
    """Prosi Jellyfin o skan bibliotek. Zwraca (sukces: bool, komunikat: str)."""
    url = (url or os.environ.get("JELLYFIN_URL") or "").strip()
    key = (key or os.environ.get("JELLYFIN_API_KEY") or "").strip()
    if not url:
        msg = "Brak adresu Jellyfina (JELLYFIN_URL) w konfiguracji"
        print(f"[jellyfin] {msg}", flush=True)
        return False, msg
    if not key:
        msg = "Brak klucza API Jellyfina (JELLYFIN_API_KEY) — jeśli dodano w opcjach HA, zrestartuj dodatek"
        print(f"[jellyfin] {msg}", flush=True)
        return False, msg
    # Nie każdy serwer przyjmuje X-Emby-Token (nginx potrafi go zgubić) —
    # wariant Authorization: MediaBrowser ***"..." oraz parametr api_key działają szerzej.
    headers = {
        "Authorization": f'MediaBrowser Token="{key}"',
        "X-Emby-Token": key,
    }
    target = f"{url.rstrip('/')}/Library/Refresh"
    print(f"[jellyfin] wysyłam żądanie skanowania biblioteki do {target}...", flush=True)
    try:
        r = requests.post(target, headers=headers, params={"api_key": key}, timeout=15)
        if r.status_code in (200, 204):
            msg = "Skan Jellyfina został uruchomiony!"
            print(f"[jellyfin] sukces (HTTP {r.status_code}): skan bibliotek wystartował", flush=True)
            return True, msg
        if r.status_code in (401, 403):
            msg = f"Błąd autoryzacji Jellyfin ({r.status_code}): nieprawidłowy klucz API"
            print(f"[jellyfin] {msg}", flush=True)
            return False, msg
        msg = f"Jellyfin zwrócił błąd HTTP {r.status_code}"
        print(f"[jellyfin] {msg}", flush=True)
        return False, msg
    except requests.RequestException as e:
        msg = f"Błąd połączenia z Jellyfin ({url}): {e}"
        print(f"[jellyfin] {msg}", flush=True)
        return False, msg


def trigger_scan(url: str | None = None, key: str | None = None) -> bool:
    """Prosi Jellyfin o skan bibliotek. Bez klucza zwraca False (nie jest błędem)."""
    ok, _ = trigger_scan_status(url, key)
    return ok
