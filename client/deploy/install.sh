#!/usr/bin/env bash
# Instalator na hoście z Jellyfinem (LXC/VM). Uruchom jako root z katalogu client/.
#
#   tar czf oa.tgz -C /sciezka/do ogladajanime-scraper-analiza/client
#   (na hoście Proxmox) pct push <vmid> oa.tgz /root/oa.tgz
#   (w LXC) tar xzf /root/oa.tgz -C /root && bash /root/client/deploy/install.sh
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Uruchom jako root."; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/ogladajanime
SERVICE_USER="${SERVICE_USER:-jellyfin}"

if ! command -v python3 >/dev/null; then
  echo "Brak python3 — zainstaluj: apt install -y python3 python3-requests"
  exit 1
fi
python3 -c 'import requests' 2>/dev/null || {
  echo "Brak modułu requests — zainstaluj: apt install -y python3-requests"
  exit 1
}

install -d -m 755 "$DEST/client"
install -m 644 "$SRC"/*.py "$DEST/client/"
[ -f "$SRC/README.md" ] && install -m 644 "$SRC/README.md" "$DEST/client/README.md"

# providers/ to warstwa zrodel — niezbedna do dzialania
install -d -m 755 "$DEST/client/providers"
install -m 644 "$SRC"/providers/*.py "$DEST/client/providers/"

if [ ! -f /etc/oa-anime.env ]; then
  install -m 600 -o root -g root "$SRC/deploy/oa-anime.env.example" /etc/oa-anime.env
  echo ">> Utworzono /etc/oa-anime.env — uzupełnij OA_LOGIN, OA_PASS, JELLYFIN_API_KEY."
fi

for u in oa-stream.service oa-kolekcja.service oa-sync.service oa-sync.timer; do
  install -m 644 "$SRC/deploy/$u" "/etc/systemd/system/$u"
  sed -i "s/^User=.*/User=$SERVICE_USER/; s/^Group=.*/Group=$SERVICE_USER/" "/etc/systemd/system/$u"
done

systemctl daemon-reload
systemctl enable --now oa-stream.service oa-kolekcja.service oa-sync.timer

sleep 2
systemctl --no-pager --lines=0 status oa-stream.service oa-kolekcja.service oa-sync.timer || true

echo
echo "Panel:  http://$(hostname -I 2>/dev/null | awk '{print $1}'):9100/"
echo "Strumień: http://$(hostname -I 2>/dev/null | awk '{print $1}'):9099/health"
echo "Logi:   journalctl -u oa-stream -u oa-kolekcja -f"
echo
echo "Pamiętaj: biblioteka Jellyfina musi wskazywać na \$OA_ROOT (z /etc/oa-anime.env)."
