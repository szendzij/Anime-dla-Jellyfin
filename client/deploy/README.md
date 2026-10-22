# Wdrożenie na hoście z Jellyfinem

## Dlaczego właśnie tam

`.strm` to zwykłe pliki — Jellyfin musi je **widzieć na swoim dysku**. Biblioteka
`Filmy` wskazuje na `/media/extDisk`, a ten katalog istnieje wyłącznie na hoście
Jellyfina (192.168.1.10). Z innej maszyny nic tam nie zapisze, bo host wystawia
tylko port `8096` (brak SSH, SMB, NFS).

Dlatego `streamd`, panel i sync uruchamiamy **na tym samym hoście co Jellyfin**.
Wtedy `OA_ROOT` to zwykła ścieżka lokalna, a `OA_BASE_URL` może zostać `127.0.0.1`.

```
LXC z Jellyfinem (192.168.1.10)
├── Jellyfin            :8096   czyta /media/extDisk
├── streamd             :9099   mintuje tokeny, 302/proxy do CDN
├── kolekcja (panel)    :9100   szukaj i dodawaj
└── oa-sync.timer       05:00   dokłada nowe odcinki
```

## Kroki

### 1. Przenieś katalog na hosta

Z hosta Proxmox (tam masz `pct`):

```bash
tar czf /tmp/oa.tgz -C /path/to/project client
pct push <vmid> /tmp/oa.tgz /root/oa.tgz
```

W LXC:

```bash
tar xzf /root/oa.tgz -C /root     # -> /root/client
bash /root/client/deploy/install.sh
```

`install.sh` kopiuje kod do `/opt/ogladajanime/client`, tworzy `/etc/oa-anime.env`,
instaluje jednostki i włącza usługi. Użytkownika usług zmienisz przez
`SERVICE_USER=<nazwa> bash install.sh` (domyślnie `jellyfin`).

### 2. Uzupełnij `/etc/oa-anime.env`

```ini
OA_LOGIN=...
OA_PASS=...
OA_ROOT=/media/extDisk/anime
OA_BASE_URL=http://127.0.0.1:9099
JELLYFIN_URL=http://127.0.0.1:8096
JELLYFIN_API_KEY=...
```

Plik ma `0600` i należy do roota. Potem:

```bash
systemctl restart oa-stream oa-kolekcja
curl -s http://127.0.0.1:9099/health
```

### 3. Uprawnienia

Użytkownik usług (`jellyfin`) musi **pisać** do `$OA_ROOT`, bo panel tworzy tam pliki:

```bash
install -d -o jellyfin -g jellyfin /media/extDisk/anime
```

### 4. Biblioteka w Jellyfinie

Dashboard → Biblioteki → Dodaj → typ **Programy TV** → folder `/media/extDisk/anime`.
Skan uruchomi się sam; później robi to panel i `oa-sync.timer`.

### 5. Panel

`http://192.168.1.10:9100/` — wyszukaj, dodaj, sprawdź, że odcinek jest w Jellyfinie
i się odtwarza.

## Uwaga o kluczu API

Ten Jellyfin (za nginx) **odrzuca** `X-Emby-Token` i `?api_key=` — działa tylko:

```
Authorization: MediaBrowser Token="<klucz>"
```

Kod wysyła oba nagłówki, więc jest odporny na obie konfiguracje.

## Diagnostyka

```bash
systemctl status oa-stream oa-kolekcja oa-sync.timer
journalctl -u oa-stream -u oa-kolekcja -n 50 --no-pager
curl -s http://127.0.0.1:9099/health
curl -s -X POST -H 'Authorization: MediaBrowser Token="<klucz>"' \
     http://127.0.0.1:8096/Library/Refresh -o /dev/null -w '%{http_code}\n'   # 204 = ok
```

## Gdy host Jellyfina nie może mieć dostępu do internetu

Wtedy odwrotnie: `streamd` zostaje na maszynie z internetem, a pliki `.strm` muszą
trafić na dysk Jellyfina przez współdzielony montaż (NFS/SMB). W `/etc/oa-anime.env`
ustawiasz wtedy `OA_BASE_URL=http://<ip-maszyny-z-internetem>:9099` i `OA_ROOT`
na zamontowaną ścieżkę.
