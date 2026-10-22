#!/usr/bin/env bash
# Składa gotowy dodatek: kopiuje kod klienta do app/ i pakuje katalog.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT="$(cd "$HERE/../client" && pwd)"

rm -rf "$HERE/app"
mkdir -p "$HERE/app"
cp "$CLIENT"/*.py "$HERE/app/"
# providers/ to warstwa źródeł (ogladajanime + CDA) — bez niej import padnie
mkdir -p "$HERE/app/providers"
cp "$CLIENT"/providers/*.py "$HERE/app/providers/"

OUT="$HERE/../oa-anime.tar.gz"   # katalog główny repo (ignorowany przez git)
tar czf "$OUT" -C "$HERE/.." oa_anime
echo "gotowe: $OUT"
tar tzf "$OUT" | head -20
