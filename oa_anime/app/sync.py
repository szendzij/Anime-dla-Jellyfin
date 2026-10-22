#!/usr/bin/env python3
"""Cron: dokłada nowe odcinki do kolekcji i prosi Jellyfin o skan.

    python3 sync.py [--data ~/.config/ogladajanime/collection.json] [--dry-run]

Dla każdego serialu z kolekcji pobiera aktualną listę odcinków i tworzy brakujące
pliki .strm. Istniejących nie rusza. Na końcu (jeśli coś doszło) uruchamia skan.

Wymaga OA_LOGIN / OA_PASS przy pierwszym uruchomieniu albo zapisanej sesji.
Skan Jellyfina wymaga JELLYFIN_URL i JELLYFIN_API_KEY (bez nich tylko pliki).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import collection
from oa import AuthError, OgladajAnimeError
from providers import build_sources


def main() -> int:
    ap = argparse.ArgumentParser(description="synchronizacja kolekcji z ogladajanime")
    ap.add_argument("--data", default="~/.config/ogladajanime/collection.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="wypisz też seriale bez zmian")
    ap.add_argument("--session-file", default=None)
    args = ap.parse_args()

    path = Path(args.data).expanduser()
    data = collection.load(path)
    series = data.get("series", {})
    if not series:
        if args.verbose:
            print("Kolekcja jest pusta — dodaj coś przez panel (kolekcja.py).")
        return 0

    kw = {"session_file": args.session_file} if args.session_file else {}
    sources = build_sources(**kw)

    # Logowanie dotyczy tylko źródeł, które go wymagają — kolekcja z samym CDA
    # musi się synchronizować nawet bez danych dostępowych ogladajanime.
    needed = {collection.entry_source(e) for e in series.values()}
    try:
        for name in needed:
            src = sources.get(name)
            if src:
                src.ensure_ready()
    except AuthError as e:
        print(f"Błąd logowania: {e}")
        return 2

    total_added = 0
    failed = 0
    for key, entry in list(series.items()):
        try:
            source = sources.get(collection.entry_source(entry))
            if source is None:
                raise OgladajAnimeError(f"nieznane źródło: {collection.entry_source(entry)}")
            res = collection.sync_series(source, key, entry)
            total_added += res["added"]
            if res["added"] or args.verbose:
                state = f"+{res['added']} nowych" if res["added"] else "bez zmian"
                print(f"  {res['title']:<40} {res['total']:>4} odc.  {state}")
        except (OgladajAnimeError, RuntimeError) as e:
            failed += 1
            print(f"  {entry.get('title', key):<40} BŁĄD: {e}")

    if args.dry_run:
        print(f"\n[dry-run] doszłoby {total_added} plików; nic nie zapisano")
        return 0

    collection.save(path, data)

    scanned = False
    if total_added:
        scanned = collection.trigger_scan()

    if not total_added and not failed:
        return 0  # tryb cron: cisza, gdy nic się nie zmieniło

    print(
        f"\nDodano {total_added} plików"
        + (", skan Jellyfina uruchomiony" if scanned else
           (", skan pominięty (brak JELLYFIN_API_KEY)" if total_added else ""))
        + (f", błędów: {failed}" if failed else "")
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
