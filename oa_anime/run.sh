#!/bin/sh
# Uruchamia redirector, panel i dobowy sync.
# Opcje czytamy wprost z /data/options.json — bez zależności od bashio/s6.
set -eu

OPTIONS=/data/options.json
DATA=/data/collection.json
export OA_SESSION_FILE=/data/session.lwp

opt() {
    python3 -c "import json,sys; print(json.load(open('$OPTIONS')).get(sys.argv[1], ''))" "$1"
}

log() { echo "[oa] $*"; }

export OA_LOGIN="$(opt login)"
export OA_PASS="$(opt password)"
export JELLYFIN_URL="$(opt jellyfin_url)"
export JELLYFIN_API_KEY="$(opt jellyfin_api_key)"
ROOT="$(opt root)"
BASE_URL="$(opt base_url)"

log "======================================================="
log "            Anime dla Jellyfin - Start                 "
log "======================================================="
log "Katalog biblioteki:  ${ROOT}"
log "Adres redirectora:   ${BASE_URL}"

if [ -n "${JELLYFIN_URL}" ] && [ -n "${JELLYFIN_API_KEY}" ]; then
    KEY_LEN=${#JELLYFIN_API_KEY}
    if [ "$KEY_LEN" -gt 8 ]; then
        MASKED_KEY="$(echo "${JELLYFIN_API_KEY}" | cut -c 1-4)...$(echo "${JELLYFIN_API_KEY}" | rev | cut -c 1-4 | rev)"
    else
        MASKED_KEY="****"
    fi
    log "Jellyfin:            ${JELLYFIN_URL} (klucz API: ${MASKED_KEY})"
elif [ -n "${JELLYFIN_URL}" ]; then
    log "Jellyfin:            ${JELLYFIN_URL} (brak klucza API — automatyczny skan wyłączony)"
else
    log "Jellyfin:            brak konfiguracji — automatyczny skan wyłączony"
fi

if [ -n "${OA_LOGIN}" ] && [ -n "${OA_PASS}" ]; then
    log "OglądajAnime:        konto '${OA_LOGIN}' skonfigurowane"
else
    log "OglądajAnime:        brak loginu/hasła (aktywne CDA i Shinden)"
fi

# true = segmenty HLS idą przez dodatek (pewniejsze, zużywa łącze)
# false = przekierowanie 302 do CDN (oszczędne)
if [ "$(opt segment_proxy)" = "True" ] || [ "$(opt segment_proxy)" = "true" ]; then
    export OA_SEGMENT_PROXY=1
    log "Proxy segmentów HLS: włączone (strumień przez dodatek)"
else
    export OA_SEGMENT_PROXY=0
    log "Proxy segmentów HLS: wyłączone (przekierowanie 302)"
fi

# true = pliki z CDA ida przez dodatek (gdyby CDN odrzucal ffmpeg)
if [ "$(opt cda_proxy)" = "True" ] || [ "$(opt cda_proxy)" = "true" ]; then
    export CDA_PROXY=1
    log "Proxy strumieni CDA: włączone (strumień przez dodatek)"
else
    export CDA_PROXY=0
    log "Proxy strumieni CDA: wyłączone (przekierowanie 302 do CDN)"
fi
log "======================================================="

mkdir -p "${ROOT}"
cd /app

log "uruchamianie serwera redirectora (:9099)..."
python3 streamd.py --host 0.0.0.0 --port 9099 &
STREAM_PID=$!

log "uruchamianie panelu WWW (:9100)..."
python3 kolekcja.py --host 0.0.0.0 --port 9100 \
    --root "${ROOT}" --base-url "${BASE_URL}" --data "${DATA}" &
PANEL_PID=$!

(
    while true; do
        sleep 60
        log "uruchamiam okresową synchronizację kolekcji..."
        python3 sync.py --data "${DATA}" -v || log "synchronizacja zakończona z błędami"
        sleep 86400
    done
) &

# watchdog: gdy ktorys z serwerow padnie, konczymy — supervisor uruchomi nas ponownie
while :; do
    sleep 30
    if ! kill -0 "${STREAM_PID}" 2>/dev/null; then
        log "redirector zakonczyl prace — restart"
        exit 1
    fi
    if ! kill -0 "${PANEL_PID}" 2>/dev/null; then
        log "panel zakonczyl prace — restart"
        exit 1
    fi
done
