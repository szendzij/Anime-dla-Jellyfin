# Dodatek HA: OgladajAnime dla Jellyfin

Chodzi obok dodatku Jellyfin w Home Assistant, ma dostęp do `/media` (map `media:rw`),
więc zapisuje pliki `.strm` dokładnie tam, gdzie Jellyfin je widzi.

## Dwa źródła

| Źródło | Wymaga | Jak dodać |
|---|---|---|
| ogladajanime.pl | `login` + `password` | wyszukiwarka w panelu |
| CDA | nic | wyszukiwarka **albo** wklejony link do folderu |
| Shinden | nic | wyszukiwarka w panelu (wideo rozwiązywane z playerów CDA) |

Bez danych ogladajanime dodatek nadal działa — zostaje samo CDA (redirector startuje,
a odcinki ogladajanime odpowiedzą wtedy błędem z powodem).

## Co robi

- `:9099` — redirector: `.strm` wskazuje tutaj, dodatek mintuje świeży token i robi
  302 do CDN (albo proxy, gdy źródło wymaga nagłówka przeglądarki),
- `:9100` — panel: wyszukaj anime, dodaj do kolekcji, wywołaj skan Jellyfina,
- raz na dobę — sync: dokłada nowe odcinki.

## Instalacja

1. Zbuduj paczkę na maszynie roboczej:

   ```bash
   bash package.sh          # -> ../oa-anime.tar.gz
   ```

2. Skopiuj katalog `oa-anime` do `/addons` w Home Assistant. Potrzebny dostęp do plików —
   najprościej dodatek **Samba share** (sklep dodatków), potem:

   ```bash
   # z maszyny roboczej, po zamontowaniu udziału
   cp -r oa-anime /mnt/ha/addons/
   ```

   Alternatywnie dodatek **Advanced SSH & Web Terminal** i `scp`.

3. Ustawienia → Dodatki → sklep → ⋮ → **Sprawdź ponownie** → w sekcji „Dodatki lokalne"
   pojawi się **OgladajAnime dla Jellyfin**. Zainstaluj.

4. W konfiguracji dodatku uzupełnij:

   | Pole | Wartość |
   |---|---|
   | `login`, `password` | konto ogladajanime |
   | `root` | `/media/extDisk/anime` (ścieżka widziana przez Jellyfin) |
   | `base_url` | `http://192.168.1.10:9099` |
   | `jellyfin_url` | `http://192.168.1.10:8096` |
   | `jellyfin_api_key` | klucz z Jellyfin → Dashboard → API Keys |
   | `segment_proxy` | `true` = segmenty HLS przez dodatek (pewniejsze, zużywa łącze) |
   | `cda_proxy` | `true` = pliki z CDA przez dodatek (gdyby CDN odrzucał ffmpeg) |

5. Uruchom. Logi: karta dodatku → **Log**.

6. W Jellyfinie: biblioteka typu **Programy TV** → `/media/extDisk/anime`.

Panel: `http://192.168.1.10:9100/`

## Uwagi

- `base_url` musi być adresem **widzianym przez Jellyfin**, czyli IP HA — nie `127.0.0.1`.
- Dodatek nie ma autoryzacji panelu; wystawiaj go tylko w LAN-ie.
- Stan (sesja, kolekcja) trzymany w `/data`, przeżywa restart dodatku.
