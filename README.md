# Anime w Jellyfin — ogladajanime · CDA · Shinden

> **EN (short):** Stream Polish-subtitled anime into Jellyfin from three portals using `.strm`
> files and a small local redirector. Nothing is downloaded: every playback gets a freshly minted
> token. Works as a Home Assistant add-on or as plain systemd services. No browser, no solver.

Trzy źródła, jeden redirector i biblioteka Jellyfin, która zachowuje się jak zwykły serial:
postęp, przewijanie, wznawianie. Odcinki **nie są pobierane na dysk** — pliki `.strm` wskazują na
lokalną usługę, która przy każdym odtworzeniu zdobywa świeży link.

## Spis treści

- [Jak to działa](#jak-to-działa)
- [Źródła i ich zasięg](#źródła-i-ich-zasięg)
- [Wymagania](#wymagania)
- [Instalacja A: dodatek Home Assistant](#instalacja-a-dodatek-home-assistant)
- [Instalacja B: osobny host (systemd)](#instalacja-b-osobny-host-systemd)
- [Konfiguracja](#konfiguracja)
- [Panel „Kolekcja"](#panel-kolekcja)
- [CLI](#cli)
- [Automatyczne dokładanie odcinków](#automatyczne-dokładanie-odcinków)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)
- [Ograniczenia](#ograniczenia)
- [Bezpieczeństwo](#bezpieczeństwo)
- [Struktura repozytorium](#struktura-repozytorium)
- [Prawne](#prawne)
- [Podziękowania](#podziękowania)

## Jak to działa

```
Jellyfin ──(.strm)──> streamd (redirector) ──302──> CDN (HLS / MP4)
   │                        │
   │                        └──(proxy)──> źródła wymagające nagłówka przeglądarki
   │
   └── biblioteka z plikami .strm: Serial/Season 01/Serial - S01E01 - tytuł.strm
```

Linki do wideo są podpisane i wygasają (godziny). Dlatego `.strm` **nie zawiera** adresu CDN,
tylko adres lokalnego redirectora:

```
http://192.168.1.10:9099/e/226672          # ogladajanime
http://192.168.1.10:9099/e/cda-23576492f   # CDA
http://192.168.1.10:9099/e/sh-MlZwaXNv…    # Shinden
```

Przy każdym odtworzeniu redirector zdobywa świeży token (z zapasem wyliczonym ze znacznika czasu
w samym tokenie) i odpowiada `302`. Dzięki temu biblioteka jest trwała, a ruch wideo nie przechodzi
przez Twój serwer — chyba że źródło tego wymaga (`*_proxy`).

Kod jest zorganizowany wokół interfejsu źródeł (`search → episodes → links`), więc redirector,
panel i cron nie wiedzą, z którego portalu pochodzi odcinek:

```
client/providers/__init__.py   wspólny interfejs + rozbiór prefiksu
client/providers/cda.py        CDA (foldery jako seriale, manifest, videoGetLink)
client/providers/shinden.py    Shinden (katalog + rozwiązywanie playerów)
client/oa.py                   ogladajanime (logowanie, sesja na dysku)
```

Prefiks w adresie odcinka jest częścią trwałego adresu w `.strm`. Ogladajanime zostaje bez
prefiksu, żeby biblioteki już zbudowane na tym projekcie nie straciły ważności.

## Źródła i ich zasięg

| Portal | Logowanie | Serial to | Odtwarzanie |
|---|---|---|---|
| **ogladajanime.pl** | wymagane (`OA_LOGIN`/`OA_PASS`) | anime ze strony | HLS lub bramka przez proxy |
| **cda.pl** | nie | folder użytkownika | MP4 albo HLS `200` dla każdego klienta |
| **shinden.pl** | nie | seria anime | tylko odcinki, których playerem jest CDA |

### Zasięg — rzecz, o której trzeba wiedzieć

**Shinden nie hostuje wideo.** To katalog odcinków i agregator odtwarzaczy: strona odcinka
wystawia kilkanaście serwisów, z których realnie odczytujemy **CDA**. Zmierzone przykłady:

| Tytuł | Wynik |
|---|---|
| Jujutsu Kaisen, sezon 1 | 3/3 próbki gra (1080p przez CDA, ~7 s na odcinek) |
| Jujutsu Kaisen, sezon 2 | 0/3 — tylko Filemoon, Vidara, Vk, Yourupload |

Zasięg zależy więc od tytułu, nie od portalu. Odcinek bez obsługiwanego serwisu zgłasza to jasno:
`brak obsługiwanego serwisu (dostępne: Filemoon, Vk, …)`.

Serwisy, których **nie** obsługujemy i dlaczego: Filemoon (odtwarzacz w JS), Vk (własne API, brak
adresów w HTML), Vidara (domena nie odpowiada), Mega (szyfrowanie po stronie klienta),
Crunchyroll (DRM), Youtube (inny charakter materiału). Gdrive i Yourupload są wykonalne.

Szczegóły techniczne każdego portalu: [`docs/`](docs/) — [ogladajanime](docs/ogladajanime-api.md),
[CDA](docs/cda-api.md), [Shinden](docs/shinden-api.md).

## Wymagania

- Python 3.10+ i `requests` (dodatek HA wnosi je sam)
- Jellyfin z obsługą `.strm` — ffmpeg jest w nim zbundlowany
- Katalog biblioteki **widziany przez Jellyfin** (pliki `.strm` muszą leżeć na jego dysku)
- `yt-dlp` opcjonalnie, wyłącznie do trybu pobierania — do streamingu niepotrzebny

## Instalacja A: dodatek Home Assistant

Zalecana droga, gdy Jellyfin chodzi jako dodatek HA — wtedy pliki `.strm` trafiają dokładnie tam,
gdzie Jellyfin je widzi, bez SSH/SMB/NFS na hoście.

### A1. Z repozytorium GitHub (najprościej)

1. **Ustawienia → Dodatki → Sklep dodatków → ⋮ → Repozytoria**
2. Dodaj `https://github.com/USER/REPO` i odśwież stronę
3. Zainstaluj **„Anime dla Jellyfin"**, uzupełnij opcje i uruchom

Supervisor sam zbuduje obraz z tego repozytorium — katalog `oa_anime/` w głównym katalogu
repozytorium jest w formacie, którego wymaga (stąd `repository.yaml`).

### A2. Ręcznie, z paczki

Przydatne, gdy nie chcesz wystawiać repozytorium albo testujesz zmiany lokalnie.

**1. Zbuduj paczkę** (z katalogu repozytorium):

```bash
bash oa_anime/package.sh                 # -> oa-anime.tar.gz w katalogu głównym
```

**2. Wgraj katalog do `/addons`** w Home Assistant. Najprościej przez dodatek
**Advanced SSH & Web Terminal** z **wyłączonym Protection mode**:

```bash
cd /addons && wget http://<IP-twojej-maszyny>:8000/oa-anime.tar.gz && tar xzf oa-anime.tar.gz --overwrite
```

> Uwaga: `wget` nie nadpisuje plików — drugie pobranie zapisze `oa-anime.tar.gz.1`, a `tar`
> rozpakuje stary plik. Kasuj archiwum przed ponownym pobraniem.

**3. Sklep → ⋮ → Sprawdź aktualizacje → Dodatki lokalne → „Anime dla Jellyfin" → Zainstaluj.**

**4. Uzupełnij konfigurację** (patrz [Konfiguracja](#konfiguracja)) i uruchom.

**5. W Jellyfinie** dodaj bibliotekę typu **Programy TV** wskazującą na `root` (np.
`/media/extDisk/anime`).

## Instalacja B: osobny host (systemd)

Gdy Jellyfin nie jest dodatkiem HA — usługi muszą chodzić **na tym samym hoście co Jellyfin**,
bo `.strm` to zwykłe pliki i muszą leżeć na jego dysku.

```bash
bash client/deploy/install.sh          # kopiuje kod, tworzy /etc/oa-anime.env, włącza usługi
$EDITOR /etc/oa-anime.env              # uzupełnij poświadczenia i ścieżki
systemctl restart oa-stream oa-kolekcja
curl -s http://127.0.0.1:9099/health
```

Szczegóły: [`client/deploy/README.md`](client/deploy/README.md).

## Konfiguracja

### Opcje dodatku HA

| Opcja | Znaczenie |
|---|---|
| `login`, `password` | konto ogladajanime. Bez nich dodatek działa dalej — zostają CDA i Shinden |
| `root` | katalog biblioteki widziany przez Jellyfin, np. `/media/extDisk/anime` |
| `base_url` | adres redirectora **widziany przez Jellyfin**, np. `http://192.168.1.10:9099` |
| `jellyfin_url`, `jellyfin_api_key` | do automatycznego skanu biblioteki po dodaniu serialu |
| `segment_proxy` | `true` = segmenty HLS idą przez dodatek; domyślnie `false` (302 do CDN) |
| `cda_proxy` | `true` = pliki z CDA idą przez dodatek; domyślnie `false` |

### Zmienne środowiskowe (tryb samodzielny)

| Zmienna | Znaczenie |
|---|---|
| `OA_LOGIN`, `OA_PASS` | poświadczenia ogladajanime (albo zapisana sesja) |
| `OA_SESSION_FILE` | ścieżka cookie-jara, domyślnie `~/.config/ogladajanime/session.lwp` (0600) |
| `JELLYFIN_URL`, `JELLYFIN_API_KEY` | skan biblioteki; bez klucza wszystko działa poza skanem |
| `OA_SEGMENT_PROXY`, `CDA_PROXY` | odpowiedniki opcji dodatku |
| `SHINDEN_PLAYER_WAIT` | przerwa na przygotowanie odtwarzacza Shindena (domyślnie 5 s) |

## Panel „Kolekcja"

```bash
python3 client/kolekcja.py --port 9100 --root ~/media/anime --base-url http://127.0.0.1:9099
```

Otwórz `http://<host>:9100/`. Wybierasz źródło, wpisujesz tytuł, klikasz **Dodaj** — panel
generuje `.strm` dla wszystkich odcinków i prosi Jellyfin o skan. Przycisk **Usuń** kasuje folder
serialu razem z wpisem. Dla CDA jest dodatkowe pole: wklejony link do folderu z odcinkami.

## CLI

```bash
# ogladajanime
python3 client/cli.py search "attack on titan"
python3 client/cli.py episodes one-piece --limit 20
python3 client/cli.py strm one-piece --episodes 1-10 --base-url http://192.168.1.10:9099 --root ~/media/anime

# CDA
python3 client/cli.py cda-search "Dragon Ball"
python3 client/cli.py cda-strm /Huunreh/folder/14062968 --base-url http://192.168.1.10:9099 --root ~/media/anime

# Shinden
python3 client/cli.py shinden-search "jujutsu kaisen"
python3 client/cli.py shinden-episodes /series/55778-jujutsu-kaisen
python3 client/cli.py shinden-strm /series/55778-jujutsu-kaisen --base-url http://192.168.1.10:9099 --root ~/media/anime
```

`--dry-run` pokazuje, co zostałoby zapisane, nic nie tworząc.

## Automatyczne dokładanie odcinków

`sync.py` dokłada `.strm` tylko dla odcinków, których jeszcze nie ma, i **milczy, gdy nic się nie
zmieniło** (żeby cron nie zasypywał Cię powiadomieniami). W dodatku HA chodzi raz na dobę; na
osobnym hoście jest `oa-sync.timer`.

```bash
python3 client/sync.py --data ~/.config/ogladajanime/collection.json -v
```

## Rozwiązywanie problemów

| Objaw | Przyczyna | Rozwiązanie |
|---|---|---|
| Jellyfin: `URL … is not in allowed_segment_extensions` | CDN podaje segmenty HLS pod `.css`, żeby blokować pobieranie | zaimplementowane: redirector przepisuje playlistę. Awaryjnie `segment_proxy: true` |
| `401` przy skanie biblioteki | `X-Emby-Token` bywa obcinany przez reverse proxy | kod wysyła też `Authorization: MediaBrowser Token="…"`; sprawdź, czy Jellyfin stoi pod proxy |
| Odcinki znikają po kilku godzinach | w `.strm` wpisano bezpośredni link do CDN | używaj `--base-url`/`base_url` — ma wskazywać redirector |
| Nic się nie odtwarza, `502` z redirectora | źródło nie oddało linku (odcinek usunięty, brak playerów) | treść odpowiedzi mówi, które serwisy są dostępne |
| Shinden: `brak obsługiwanego serwisu (dostępne: …)` | odcinek nie ma playera CDA | zasięg zależy od tytułu — patrz [Źródła i ich zasięg](#źródła-i-ich-zasięg) |
| Shinden: odcinek ładuje się ~5 s | Shinden przygotowuje odtwarzacz asynchronicznie | tak ma być; wynik jest cache'owany |
| Dodatek nie startuje, „brak loginu/hasła" | puste `login`/`password` przy źródle ogladajanime | dodatek działa dalej na CDA i Shindenie; uzupełnij, by wrócić do ogladajanime |
| Dodatek wpada w pętlę restartów | port zajęty przez inną usługę | w logu jest wprost, który port i jak go sprawdzić (`ss -lntp`) |
| `cd /addons: No such file or directory` | oficjalny dodatek „Terminal & SSH" nie montuje `/addons` | użyj „Advanced SSH & Web Terminal" (Protection mode off) albo Samby |

## Ograniczenia

- **Shinden to katalog** — odtwarzalny jest tylko wątek, który prowadzi do CDA (omówione wyżej).
- **Materiały HD w CDA** bywają za kontem; anonimowo działa 360p–1080p w zależności od pliku.
- **Wyszukiwarka CDA** jest słaba dla tytułów anime — pewniejsze jest wklejenie linku do folderu.
- **Panel nie ma autoryzacji** — przeznaczony do sieci lokalnej.
- Odcinki stare i usunięte (np. brak źródeł na ogladajanime) są pomijane z komunikatem, nie
  przerywają całej operacji.

## Bezpieczeństwo

- Poświadczenia ogladajanime trzymane są wyłącznie w zmiennych środowiskowych albo w pliku sesji
  z uprawnieniami `0600`. Hasło nie jest zapisywane w repozytorium ani w kolekcji.
- Klucz API Jellyfina trafia do `/etc/oa-anime.env` (0600) lub opcji dodatku.
- `streamd` i panel nie mają autoryzacji: każdy, kto dosięgnie portu `9099`/`9100`, może odtwarzać
  i dodawać treści. Trzymaj je w LAN-ie albo postaw przed nimi reverse proxy z uwierzytelnianiem.

## Struktura repozytorium

```
├── client/                 usługa w Pythonie
│   ├── providers/          źródła: wspólny interfejs, CDA, Shinden
│   ├── streamd.py          redirector (mintuje tokeny, przepisuje playlisty)
│   ├── kolekcja.py         panel WWW
│   ├── collection.py       wspólna logika kolekcji
│   ├── sync.py             dokładanie nowych odcinków (cron)
│   ├── cli.py              CLI dla wszystkich źródeł
│   ├── jellyfin.py         nazewnictwo, zapis .strm, pobieranie
│   ├── oa.py               klient ogladajanime
│   └── deploy/             jednostki systemd + instalator
├── oa_anime/               dodatek Home Assistant (app/ generuje package.sh)
├── repository.yaml         wpis dla sklepu dodatków HA
└── docs/                   rozpoznanie API trzech portali
```

## Prawne

Projekt nie hostuje ani nie rozpowszechnia żadnych treści — tworzy wyłącznie pliki `.strm`
wskazujące na materiały już dostępne publicznie w sieci. Korzystaj z niego na własną
odpowiedzialność i tylko z treści, do których masz prawo dostępu, używając własnych kont.
Wspieraj twórców i legalne serwisy streamingowe.

## Podziękowania

- [k0aziu/ogladajanime-cli](https://github.com/k0aziu/ogladajanime-cli) (MIT) — mapa endpointów
  ogladajanime; implementacja w tym projekcie jest nowa (sesja na dysku, parser oparty na
  `HTMLParser`, timeouty i retry).
- [Tsugumik/shinden-pl-api-rs](https://github.com/Tsugumik/shinden-pl-api-rs) — wskazówka do
  kolejności wywołań `player_load` / `player_show` w API Shindena.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — ekstraktor CDA posłużył jako dokumentacja
  sposobu wydobycia linku (`videoGetLink`).

## Licencja

[MIT](LICENSE).
