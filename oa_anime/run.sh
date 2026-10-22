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

# true = segmenty HLS idą przez dodatek (pewniejsze, zużywa łącze)
# false = przekierowanie 302 do CDN (oszczędne)
if [ "$(opt segment_proxy)" = "True" ] || [ "$(opt segment_proxy)" = "true" ]; then
    export OA_SEGMENT_PROXY=1
    log "segmenty HLS: proxy przez dodatek"
else
    export OA_SEGMENT_PROXY=0
    log "segmenty HLS: przekierowanie 302"
fi

log "biblioteka: ${ROOT}"
log "redirector dla .strm: ${BASE_URL}"

if [ -z "${OA_LOGIN}" ] || [ -z "${OA_PASS}" ]; then
    log "brak loginu/hasla ogladajanime — dziala tylko zrodlo CDA"
fi

# true = pliki z CDA ida przez dodatek (gdyby CDN odrzucal ffmpeg)
if [ "$(opt cda_proxy)" = "True" ] || [ "$(opt cda_proxy)" = "true" ]; then
    export CDA_PROXY=1
    log "CDA: strumien przez dodatek"
else
    export CDA_PROXY=0
    log "CDA: przekierowanie 302 do CDN"
fi

mkdir -p "${ROOT}"
cd /app

log "start redirectora :9099"
python3 streamd.py --host 0.0.0.0 --port 9099 &
STREAM_PID=$!

log "start panelu :9100"
python3 kolekcja.py --host 0.0.0.0 --port 9100 \
    --root "${ROOT}" --base-url "${BASE_URL}" --data "${DATA}" &
PANEL_PID=$!

(
    while true; do
        sleep 60
        log "sync odcinkow"
        python3 sync.py --data "${DATA}" || log "sync nieudany"
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
