#!/usr/bin/env python3
"""CLI: przeglądanie ogladajanime.pl i wystawianie odcinków do Jellyfin.

Przykłady:
    ./cli.py episodes one-piece
    ./cli.py strm one-piece --episodes 1-3
    ./cli.py strm one-piece --all --root /mnt/media/anime
    ./cli.py download one-piece --episodes 1181 --quality 1080
    ./cli.py search "attack on titan"

Poświadczenia: zmienne OA_LOGIN / OA_PASS albo pytanie interaktywne.
Sesja: ~/.config/ogladajanime/session.lwp (0600).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from oa import AuthError, OgladajAnimeError, Episode, pick_best
from jellyfin import download_media, target_path, write_strm, write_strm_url
from providers import CDA, OA, SHINDEN, CdaError, ShindenError, build_sources

DEFAULT_ROOT = Path("~/media/ogladajanime").expanduser()


def parse_range(spec: str, episodes: list[Episode]) -> list[Episode]:
    """'1-3,7' albo 'all' -> lista odcinków."""
    if spec.lower() == "all":
        return [e for e in episodes if e.season == 1]
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))
    return [e for e in episodes if e.season == 1 and e.number in wanted]


def series_label(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title()


def title_for(source, ref: str, override: str | None = None) -> str:
    """Nazwa serialu do nazw plików.

    Dla CDA bierzemy prawdziwy tytuł folderu („5. Super Mission"), bo ścieżka
    /user/folder/12345 nie mówi Jellyfinowi nic sensownego.
    """
    if override:
        return override
    describe = getattr(source.api, "describe", None)
    if describe is not None:
        try:
            return describe(ref)
        except (CdaError, ShindenError):
            pass
    return series_label(ref)


def cmd_episodes(source, args) -> int:
    eps = source.episodes(args.slug)
    main = [e for e in eps if e.season == 1]
    specials = [e for e in eps if e.season == 0]
    name = title_for(source, args.slug)
    print(f"{name}: {len(main)} odcinków, {len(specials)} dodatkowych")
    for e in main[: args.limit]:
        print(f"  S01E{e.number:02d}  {e.ep_id:>8}  {e.title[:70]}")
    if specials:
        print("  dodatkowe:")
        for e in specials[:10]:
            print(f"  S00E{e.number:02d}  {e.ep_id:>8}  {e.title[:70]}")
    return 0


def cmd_search(source, args) -> int:
    source.ensure_ready()
    for item in source.search(args.query):
        print(f"{item['ref']}\t{item['title']}")
    return 0


def _collect_targets(source, args) -> list[tuple[Episode, object]]:
    eps = source.episodes(args.slug)
    chosen = parse_range(args.episodes, eps)
    if not chosen:
        raise OgladajAnimeError("Nie wybrano żadnego odcinka (sprawdź --episodes).")

    pairs = []
    for ep in chosen:
        links = source.links(ep.ep_id)
        if not links:
            print(f"  ! S01E{ep.number:02d}: brak dostępnych źródeł, pomijam")
            continue
        link = pick_best(links, args.quality)
        if link is None:
            print(f"  ! S01E{ep.number:02d}: brak jakości {args.quality}p")
            continue
        pairs.append((ep, link))
    return pairs


def cmd_strm(source, args) -> int:
    source.ensure_ready()
    series = title_for(source, args.slug, getattr(args, "title", None))
    root = Path(args.root).expanduser()

    # Tryb redirectora: .strm wskazuje na lokalny serwer, który sam odświeża token.
    # Nie odpytujemy serwisu o każdy odcinek — dlatego generowanie 1000+ plików jest szybkie.
    if args.base_url:
        base = args.base_url.rstrip("/")
        eps = parse_range(args.episodes, source.episodes(args.slug))
        if not eps:
            raise OgladajAnimeError("Nie wybrano żadnego odcinka (sprawdź --episodes).")
        for ep in eps:
            url = source.strm_url(base, ep.ep_id)
            if args.dry_run:
                print(f"  {target_path(root, series, ep)}  ->  {url}")
            else:
                write_strm_url(root, series, ep, url)
        if not args.dry_run:
            print(f"  zapisano {len(eps)} plików .strm w {root}")
        return 0

    pairs = _collect_targets(source, args)
    for ep, link in pairs:
        if args.dry_run:
            print(f"  {target_path(root, series, ep)}")
            print(f"      -> {link.src[:110]}")
        else:
            path = write_strm(root, series, ep, link)
            print(f"  zapisano {path.relative_to(root)}  ({link.res}p)")
    print(f"\n{len(pairs)} plików .strm w {root}")
    return 0


def cmd_download(source, args) -> int:
    source.ensure_ready()
    series = title_for(source, args.slug, getattr(args, "title", None))
    pairs = _collect_targets(source, args)
    root = Path(args.root).expanduser()
    ok = 0
    for ep, link in pairs:
        # UWAGA: nie używamy with_suffix("") — tytuły bywają z kropką
        # ("... 1124.Podsumowanie sprzed przerwy") i pathlib uciąłby nazwę.
        strm_path = target_path(root, series, ep)
        target = strm_path.with_name(strm_path.name.removesuffix(".strm"))
        if args.dry_run:
            print(f"  {target}  <- {link.res}p")
            continue
        print(f"  S01E{ep.number:02d}: {link.res}p -> {target.name}")
        try:
            out = download_media(target, link, max_segments=args.max_segments)
            print(f"     OK {out.name} ({out.stat().st_size / 2**20:.1f} MiB)")
            ok += 1
        except Exception as e:  # noqa: BLE001 — chcemy kontynuować resztę kolejki
            print(f"     BŁĄD: {e}")
    print(f"\n{ok}/{len(pairs)} pobranych")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ogladajanime.pl -> Jellyfin")
    ap.add_argument("--session-file", default=None, help="ścieżka cookie-jara")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("episodes", help="wypisz odcinki")
    p.add_argument("slug")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_episodes, source=OA)

    p = sub.add_parser("search", help="szukaj anime")
    p.add_argument("query")
    p.set_defaults(fn=cmd_search, source=OA)

    for name, fn, helptext in (
        ("strm", cmd_strm, "wygeneruj pliki .strm"),
        ("download", cmd_download, "pobierz odcinki na dysk"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("slug")
        p.add_argument("--episodes", default="all", help="np. 1-3,7 albo all")
        p.add_argument("--quality", type=int, default=None, help="np. 1080")
        p.add_argument("--root", default=str(DEFAULT_ROOT))
        p.add_argument("--dry-run", action="store_true")
        p.add_argument(
            "--base-url",
            default=None,
            help="adres redirectora (streamd.py), np. http://192.168.1.10:9099",
        )
        p.add_argument(
            "--max-segments",
            type=int,
            default=None,
            help="pobierz tylko N segmentów HLS (szybki podgląd)",
        )
        p.set_defaults(fn=fn, source=OA)

    # --- CDA: bez logowania, źródłem jest folder albo pojedynczy film ---
    p = sub.add_parser("cda-search", help="szukaj na CDA (filmy i foldery)")
    p.add_argument("query")
    p.set_defaults(fn=cmd_search, source=CDA)

    p = sub.add_parser("cda-episodes", help="wypisz odcinki folderu CDA")
    p.add_argument("slug", help="ścieżka folderu, np. /user/folder/12345")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_episodes, source=CDA)

    p = sub.add_parser("cda-strm", help="wygeneruj .strm z folderu CDA")
    p.add_argument("slug", help="ścieżka folderu albo /video/<id>")
    p.add_argument("--episodes", default="all", help="np. 1-3,7 albo all")
    p.add_argument("--title", default=None, help="nazwa serialu (domyślnie tytuł folderu)")
    p.add_argument("--quality", type=int, default=None)
    p.add_argument("--root", default=str(DEFAULT_ROOT / "cda"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--base-url", default=None, help="adres redirectora, np. http://IP:9099")
    p.add_argument("--max-segments", type=int, default=None)
    p.set_defaults(fn=cmd_strm, source=CDA)

    # --- Shinden: katalog odcinków, źródłem wideo jest player CDA ---
    p = sub.add_parser("shinden-search", help="szukaj serii na Shindenie")
    p.add_argument("query")
    p.set_defaults(fn=cmd_search, source=SHINDEN)

    p = sub.add_parser("shinden-episodes", help="wypisz odcinki serii z Shindena")
    p.add_argument("slug", help="ścieżka serii, np. /series/55778-jujutsu-kaisen")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_episodes, source=SHINDEN)

    p = sub.add_parser("shinden-strm", help="wygeneruj .strm z serii na Shindenie")
    p.add_argument("slug", help="ścieżka serii, np. /series/55778-jujutsu-kaisen")
    p.add_argument("--episodes", default="all", help="np. 1-3,7 albo all")
    p.add_argument("--title", default=None, help="nazwa serii (domyślnie tytuł ze Shindena)")
    p.add_argument("--quality", type=int, default=None)
    p.add_argument("--root", default=str(DEFAULT_ROOT / "shinden"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--base-url", default=None, help="adres redirectora, np. http://IP:9099")
    p.add_argument("--max-segments", type=int, default=None)
    p.set_defaults(fn=cmd_strm, source=SHINDEN)

    args = ap.parse_args(argv)
    kw = {"session_file": Path(args.session_file).expanduser()} if args.session_file else {}
    sources = build_sources(**kw)
    source = sources[getattr(args, "source", OA)]

    try:
        return args.fn(source, args)
    except AuthError as e:
        print(f"Błąd logowania: {e}", file=sys.stderr)
        return 2
    except (OgladajAnimeError, CdaError) as e:
        print(f"Błąd: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
