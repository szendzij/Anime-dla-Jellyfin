# Anime w Jellyfin (streaming) — ogladajanime.pl + CDA

Dwa źródła pod jednym redirectorem, który pozwala **oglądać** odcinki z poziomu Jellyfin
bez pobierania ich na dysk. Mapa endpointów ogladajanime pochodzi z analizy
`k0aziu/ogladajanime-cli`; ścieżka CDA powstała z własnego rozpoznania (patrz `../docs/cda-api.md`).

| Źródło | Logowanie | Serial = | Prefiks odcinka |
|---|---|---|---|
| ogladajanime.pl | wymagane (`OA_LOGIN` / `OA_PASS`) | slug anime | pusty → `/e/226672` |
| cda.pl | **nie** | folder użytkownika | `cda-` → `/e/cda-23576492f` |
| shinden.pl | **nie** | seria anime | `sh-` → `/e/sh-<b64 ścieżki>` |

Shinden to katalog: sam nie hostuje wideo, tylko wskazuje odtwarzacze
(patrz `../docs/shinden-api.md`). Odcinki, których playerem jest CDA, grają przez
nasz provider CDA; pozostałe serwisy zgłaszamy jako nieobsługiwane.

Prefiks jest częścią trwałego adresu w `.strm`. Ogladajanime zostaje bez prefiksu, bo biblioteka
już zawiera pliki z gołym numerem odcinka — zmiana unieważniłaby całą kolekcję.

## Jak to działa

```
Jellyfin  --(.strm)-->  streamd.py  --302-->  CDN (HLS)
                             |
                             +--(proxy)-->  bramka VK (mp4)
```

Plik `.strm` nie zawiera linku do CDN, tylko adres lokalnego redirectora:

```
http://192.168.1.20:9099/e/412634
```

`streamd.py` przy każdym odtworzeniu zdobywa **świeży token** i:
- dla HLS (`master.m3u8`) → odpowiada `302` na CDN (zero transferu przez serwer),
- dla bramek typu `vkuser.net` (odcinki hostowane na VK) → **proxuje** strumień
  z nagłówkiem UA przeglądarki, bo bez niego źródło zwraca `400`.

Dzięki temu `.strm` nie wygasa: token odświeża się sam, z 10-minutowym zapasem
wyliczonym ze znacznika czasu w samym tokenie.

## Wymagania

- Python 3.10+, `requests`
- Jellyfin (dowolna wersja z obsługą `.strm`) — ffmpeg jest w nim zbundlowany
- opcjonalnie `yt-dlp` — tylko dla trybu pobierania, do streamingu niepotrzebny

## Kod źródłowy źródeł (`providers/`)

```
providers/__init__.py   wspólny interfejs: search -> episodes -> links + rozbiór prefiksu
providers/cda.py        klient CDA (foldery, manifest, videoGetLink)
oa.py                   klient ogladajanime (starszy, ale wciąż pod tym samym interfejsem)
```

Reszta programu (redirector, panel, cron, CLI) nie wie, z którego portalu pochodzi odcinek —
pyta tylko `Source`, a ten sam buduje adres `.strm` przez `strm_url()`.

## Poświadczenia i sesja

```bash
export OA_LOGIN='twoj_login'
export OA_PASS='twoje_haslo'
```

Hasła nie zapisujemy nigdzie. Po pierwszym logowaniu ciasteczka lądują w
`~/.config/ogladajanime/session.lwp` (uprawnienia `0600`), więc kolejne uruchomienia
nie potrzebują hasła. Zmienna `OA_SESSION_FILE` zmienia tę ścieżkę.

## Uruchomienie

```bash
# 1. redirector (na hoście z dostępem do internetu — może być LXC z Jellyfinem)
python3 streamd.py --port 9099

# 2. pliki .strm (szybko: nie odpytuje serwisu o każdy odcinek)
python3 cli.py strm one-piece --episodes all \
    --base-url http://192.168.1.20:9099 \
    --root ~/media/anime
```

`--episodes` przyjmuje `all`, `1-3,7` itd. Adres w `--base-url` musi być **IP widzianym
przez Jellyfin**, nie `127.0.0.1` (chyba że oba działają na tym samym hoście).

Bez `--base-url` działa tryb „na teraz": `.strm` dostaje bezpośredni link z tokenem,
ważny kilka godzin.

## CDA — drugie źródło

```bash
python3 cli.py cda-search "Dragon Ball"              # filmy i foldery
python3 cli.py cda-episodes /Huunreh/folder/14062968 # lista odcinków folderu
python3 cli.py cda-strm /Huunreh/folder/14062968 \
    --base-url http://192.168.1.20:9099 --root ~/media/anime
```

Na CDA serialem jest **folder użytkownika** — jego zawartość jest serwowana w HTML,
więc nie potrzeba ani API, ani przeglądarki. Tytuł folderu staje się nazwą serialu,
a numery odcinków czytamy z tytułów pozycji; gdy ich nie ma, numerujemy po kolei.

Odtwarzanie: `videoGetLink` zwraca manifest DASH w profilu *on-demand*, którego
`<BaseURL>` to jeden plik MP4 z indeksem do przewijania — rozbieramy go i podajemy
Jellyfinowi zwykły plik. `302` domyślnie (ffmpeg pobiera sam), a `CDA_PROXY=1`
przesyła strumień przez dodatek, gdyby CDN zaczął odrzucać ffmpeg.

Zastrzeżenie: CDA to portal wideo, a nie katalog anime — foldery zakładają użytkownicy,
więc trafiają się zarówno pełne serie, jak i śmieci. Do strumienia nadaje się zawsze,
do wyboru treści potrzebny jest człowiek.

## Panel „Kolekcja" — wyszukiwanie i dodawanie

```bash
python3 kolekcja.py --port 9100 \
    --root ~/media/anime \
    --base-url http://192.168.1.20:9099
```

Otwórz `http://<host>:9100/` — wpisujesz tytuł, dostajesz wyniki z okładkami,
klikasz **Dodaj**. Panel generuje `.strm` dla wszystkich odcinków i (jeśli zna klucz API)
woła Jellyfin o skan. Przycisk **Usuń** kasuje folder serialu i wpis z kolekcji.

Stan kolekcji: `~/.config/ogladajanime/collection.json`. Sekrety Jellyfina
(`JELLYFIN_URL`, `JELLYFIN_API_KEY`) trzymaj w `~/.config/ogladajanime/jellyfin.env` (0600)
— `sync.py` czyta go przez wrapper crona. Bez klucza panel działa, tylko nie uruchomi skanu.

## Cron — nowe odcinki same się dokładają

```bash
bash ~/.hermes/scripts/oa_sync.sh        # ręcznie
bash ~/.hermes/scripts/oa_sync.sh -v     # z listą seriali bez zmian
```

`sync.py` przechodzi po kolekcji, dolicza brakujące `.strm` i woła skan. W trybie crona
milczy, gdy nic się nie zmieniło — dostajesz wiadomość tylko, gdy coś doszło.

Zadanie Hermesa: `ogladajanime — kolekcja`, codziennie 05:00, dostawa na Telegram.

## Jellyfin

1. Dodaj bibliotekę typu **Programy TV** wskazującą na `--root`.
2. Jellyfin czyta `.strm` natywnie — nie trzeba żadnego pluginu.
3. Nazewnictwo: `<Serial>/Season 01/<Serial> - S01E01 - <tytuł>.strm`,
   materiały dodatkowe (trailer, powtórzone numery): `<Serial>/Specials/... - S00E01 - ...`.
4. Po wygenerowaniu plików uruchom skan biblioteki.

Podgląd stanu redirectora: `GET /health` → `{"ok":true,"cached":N}`, pojedynczy odcinek: `GET /e/<ep_id>`.

## Tryb pobierania (opcjonalny)

```bash
python3 cli.py download one-piece --episodes 1180 --root ~/media/anime
python3 cli.py download one-piece --episodes 1180 --max-segments 3   # szybki podgląd
```

Pobiera HLS własnym kodem (bez ffmpeg) albo przez `yt-dlp`, jeśli jest w `PATH`.
Odcinek 1080p to ~1,4 GB, więc przy bibliotece sezonowej to niepraktyczne — stąd streaming.

## systemd

```ini
# /etc/systemd/system/oa-stream.service
[Unit]
Description=ogladajanime stream redirector
After=network-online.target

[Service]
User=media
EnvironmentFile=/etc/oa-stream.env
ExecStart=/usr/bin/python3 /opt/ogladajanime/client/streamd.py --port 9099
Restart=always

[Install]
WantedBy=multi-user.target
```

`/etc/oa-stream.env` z `OA_LOGIN=`, `OA_PASS=` i uprawnieniami `0600` — hasło w samym
unicie byłoby widoczne w `systemctl show`.

## Czego ten klient pilnuje w porównaniu z oryginałem

| Problem w oryginale | Tutaj |
|---|---|
| regex zależny od kolejności atrybutów | `HTMLParser`, czyta atrybuty tagu `<li>` |
| numer odcinka z pozycji w dokumencie | numer z atrybutu `value` (1-based) |
| `split(", ")` na `set-cookie` | cookie-jar `http.cookiejar` |
| logowanie przy każdym starcie | sesja na dysku, `0600` |
| brak timeoutów | `(10, 45)` s na każde żądanie |
| brak retry | 3 próby z backoffem, respektuje `Retry-After` |
| brak limitu żądań | min. 1 s między żądaniami |
| nazwy `Episode N.mp4` | `SxxExx` + katalog sezonu, zgodnie z Jellyfin |
| yt-dlp bez `-c`/`--no-playlist` | `--continue --no-playlist`, a bez yt-dlp własny HLS |
| błędy połykane | własne wyjątki, kody wyjścia 0/1/2 |

## Ograniczenia (stan 2026-10-20)

- **Dwa typy źródeł.** Część odcinków to czysty HLS (`type: application/x-mpegURL`),
  część to bramka VK (`type: video/mp4`, `src` na `vkuser.net`). Bramka wymaga UA
  przeglądarki — dlatego istnieje tryb proxy.
- **Stare odcinki bywają bez źródeł.** `:8443/Player/<ep_id>` zwraca wtedy `404`;
  redirector odpowiada `404` z komunikatem.
- **Proxy zużywa łącze serwera** — dla odcinków VK transfer idzie przez redirector.
- Endpoint `:8443` wymaga zalogowanej sesji (ciasteczka); Bearer żądany na rootcie
  nie jest potrzebny do `/Player/<id>`.
- `get_search` obsługuje jedną stronę wyników; przy popularnych tytułach warto podać
  pełniejszą frazę.
- Serwis bywa zmieniany bez ostrzeżenia — kontrakt API może przestać się zgadzać.

## Bezpieczeństwo

- `streamd.py` nie ma autoryzacji — wystawia strumienie każdemu, kto dosięgnie portu.
  Trzymaj go w LAN-ie; jeśli ma być dostępny szerzej, postaw przed nim reverse proxy z auth.
- Nie commituj `session.lwp`.
