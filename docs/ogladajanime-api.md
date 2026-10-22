# Analiza: k0aziu/ogladajanime-cli (v1.1, commit f637747, 27.10.2025)

Klon: `repo/`. Kod: 1 plik `index.js`, 519 linii, 15 486 B. Zero testów, zero typów, zero lintera.

## Werdykt

Jako skrypt-hobby: **działa i robi robotę** — API zostało rozpracowane poprawnie, a to jest
najtrudniejsza część. Jako fundament pod plugin Jellyfin: **nadaje się wyłącznie jako
dokumentacja API**, nie jako kod do rozbudowy. Struktura (5 czystych endpointów) jest dobra,
wszystko wokół niej — obsługa błędów, konfiguracja, nazewnictwo plików, zależności — jest
prototypowa i trzeba to przepisać, nie łatać.

## Powierzchnia API (najcenniejsze, co wnosci ten kod)

| Endpoint | Metoda | Payload | Zwraca |
|---|---|---|---|
| `/manager.php?action=login` | POST | `login`, `pass` | `{data:"OK"}` + cookies `user_id`, `user_key` |
| `/manager.php?action=get_anime_names` | GET | — | `{json:[tytuł,...]}` |
| `/manager.php?action=get_search` | POST | `page`, `search_type=name`, `search` | `{data:html}` ze slugami `/anime/<slug>` |
| `/anime/<slug>` | GET | — | HTML, `<li ... value="N" title="..." ep_id="ID">` |
| `:8443/Player/<ep_id>` | GET | — | `[{label, res, src}]` |

Wymagane nagłówki: `X-Requested-With`, `Origin`, `Referer`, UA. Player siedzi na **osobnym
porcie 8443** — to nieoczywiste i bez tego kodu trudne do odkrycia.

## Co zweryfikowałem empirycznie

1. **Regex odcinków działa na dzisiejszym HTML.** Strona `/anime/one-piece` odpowiada
   anonimowo (HTTP 200, 3 515 114 B). Regex z linii 153–154 daje 1194 dopasowania przy
   dokładnie 1194 `list-group-item` i 1194 `ep_id` w dokumencie — 100% pokrycia.
2. **Numeracja odcinków jest poprawna.** HTML jest uporządkowany od najstarszego, a `value`
   to numer odcinka 1-based (`value="1"` = odcinek 1); `value="0"` ma tylko trailer „Zapowiedź”.
   Skrypt liczy odcinki własnym licznikiem z pominięciem trailera, więc trafia w te same liczby:
   `Episode 1.mp4` dla odcinka 1, `Episode 1181.mp4` dla ostatniego. Nie ma off-by-one — ale
   `value` jest ignorowane, mimo że jest autorytatywnym numerem (patrz niżej).
3. **Świeży klon wywala się natychmiast.** `config.json` jest w repo z prywatną ścieżką autora:
   ```json
   { "downloads_path": "/home/k0aziu/Downloads/Anime" }
   ```
   `loadConfig()` robi `fs.mkdirSync(config.downloads_path, {recursive:true})` → `EACCES`.
   Odtworzone: `Error: EACCES: permission denied, mkdir '/home/k0aziu/Downloads/Anime'`, EXIT=1.
   Skutek: gałąź tworząca domyślny config (linie 21–26) jest **martwym kodem dla każdego**.
   Dodatkowo `.gitignore` zawiera tylko `node_modules`, więc plik z danymi lokalnymi jest wersjonowany.
4. **Dwie z czterech zależności są nieużywane i podszywają się pod moduły Node.**
   ```json
   "querystring": "^0.2.1",
   "zlib": "^1.0.5"
   ```
   `grep` po `index.js`: zero wystąpień. Oba to npm-owe atrapy wbudowanych modułów core —
   klasyczny antywzorzec (mylą resolver, dokładają martwy kod). Do tego `figlet` ciągnie
   `commander@14`, które wymaga Node ≥ 20, choć `engines` w `package.json` nie istnieje.

## Błędy i ryzyka (z cytatami)

### Parsowanie HTML regexem — kruche, ale na dziś działa
```js
const regex =
  /<li.*?class="[^"]*list-group-item[^"]*".*?title="(.*?)".*?ep_id="(\d+)".*?>/gs;
```
Zależy od **kolejności atrybutów** (`class` → `title` → `ep_id`). Dziś między `class` a `title`
siedzi `onclick` i `value`, więc `.*?` je przeskakuje. Każda zmiana kolejności atrybutów w szablonie
albo `"` w tytule odcinka rozkłada parser. `value="(\d+)"` — prawdziwy numer odcinka — leży
w tym samym `<li>` i jest ignorowany na rzecz liczenia w pętli.

Ten sam wzorzec w wyszukiwaniu (linia 146):
```js
const matches = [...htmlContent.matchAll(/\/anime\/([a-zA-Z0-9_-]+)/g)];
```
Skoro serwer zwraca HTML, lepszy byłby selektor DOM (`cheerio`) — jedna zależność zamiast
kruchej gramatyki.

### Obsługa ciasteczek — działa przez przypadek
```js
const authCookies = loginCookieHeader.split(", ");
const userId = authCookies.find((c) => c.startsWith("user_id="));
```
`set-cookie` jest tu zlepionym stringiem. `split(", ")` tnie atrybut `Expires` w środku daty
(`expires=Wed, 21 Oct ...`). Uratowane tylko dlatego, że dalej jest `.split(";")[0]`:
```
> user_id=4217; expires=Wed      ← obcięte, ale .split(";")[0] ratuje wartość
```
Brak cookie-jara, brak obsługi wygaśnięcia, brak zapisu sesji → **każde uruchomienie wymaga
wpisania hasła**. Dla pluginu w Jellyfin to dyskwalifikujące: potrzebna sesja trwała i odświeżana.

### Brak timeoutów i retry
`fetch` w `apiRequest` nie ma `AbortSignal.timeout()` — zawieszona odpowiedź wisi w nieskończoność.
Jedyny „retry" w całym kodzie:
```js
await new Promise((resolve) => setTimeout(resolve, 1000));
```
w `findFirstValidEpisodeLinks`. Pętla pobierania (linia 336) leci bez limitu i bez backoffu:
N+1 żądań do `:8443` na serię, ryzyko 429/blokady.

### `findFirstValidEpisodeLinks` — zgadywanie jakości
```js
for (const episode of episodes.filter((ep) => ep.type === "episode")) {
  const links = await fetchEpisodeLinks(cookies, episode.ep_id, animeSlug);
  return links;   // pierwszy działający odcinek = lista jakości dla całej serii
}
```
Założenie „każdy odcinek ma te same jakości" jest jawnie łamane 40 linii dalej:
```js
if (!targetLink) {
  console.log(`[error] Jakość ${selectedQuality}p dla tego odcinka nie istnieje, pomijam.`);
```
Czyli pre-check kosztuje dodatkowe żądanie, a i tak nie gwarantuje niczego.

### Pobieranie — brak kontroli nad yt-dlp
```js
const ytDlp = spawn("yt-dlp", ["-o", destinationPath, videoUrl]);
```
Brak `-f` (wybór formatu), brak `-c` (wznowienie), brak `--no-playlist`, brak parsowania
postępu, brak retry. Rozszerzenie jest zahardkodowane:
```js
const fileName = episode.type === "trailer"
  ? `Trailer ${episode.number}.mp4`
  : `Episode ${episode.number}.mp4`;
```
Jeśli yt-dlp wyciągnie mkv/webm, mamy plik `.mp4` z innym kontenerem. Nazwa `Episode 1.mp4`
bez paddingu i bez `SxxExx` **nie spełnia konwencji Jellyfin** — to główna luka, jeśli miało
być pod serwer mediów. Traile lądują w tym samym katalogu co odcinki, więc Jellyfin spróbuje
je zindeksować jako odcinki.

### Strumieniowanie — błędy połykane
```js
const ytDlp = spawn("yt-dlp", ["-o", "-", videoUrl]);
const mpv = spawn("mpv", ["-"]);
ytDlp.stdout.pipe(mpv.stdin);
...
mpv.on("close", (code) => { ...; resolve(); });
```
Nasłuchiwany jest tylko event `'error'` (nieudany spawn), a nie kod wyjścia yt-dlp. Jeśli yt-dlp
padnie z kodem ≠ 0, `mpv` zamyka się normalnie i Promise ** rozwiązuje się sukcesem** — awaria
jest niewidoczna. Podobnie:
```js
} catch (e) {
  console.error(`\n[error]: Nie udało się pobrać linków ... lmao.`);
}
```
`e` nie jest logowane ani przerzucane — tracimy przyczynę. W `getAnimeNameSuggestions`
`catch` zwraca `[]`, więc przy awarii autocomplete po prostu milknie bez komunikatu.

### Konfiguracja i kod wyjścia
```js
const CONFIG_PATH = path.join(__dirname, "config.json");
```
ale `downloads_path` jest używane jako ścieżka relatywna do **CWD** (`path.join(config.downloads_path, ...)`),
czyli zachowanie zależy od tego, skąd odpalono skrypt — niespójne.
```js
} finally {
  console.log("\nNara złamasie!");
  exit();
}
```
`exit()` bez argumentu = kod 0 **nawet po błędzie**. Dla crona/systemd bezużyteczne.

## KISS / DRY

- **KISS:** podział na funkcje jest sensowny i czytelny; `apiRequest` jako jedna brama do HTTP —
  dobre. Ale `playVideo`/`downloadVideo` dublują logikę spawnu yt-dlp (nasłuch `close`/`error`,
  obsługa kodu wyjścia) — powinna być jedna funkcja `runYtDlp(args) : Promise`.
- **DRY:** `handleStreaming` i `handleDownloading` powtarzają blok `fetch + sort po res + prompts(select)`
  (linie 257–274 vs 290–312). Kandydat na `pickQuality()`.
- **Magiczne liczby/stringi:** port `8443`, `page: 1`, `"mp4"`, próg jakości `b.res`.
- **Martwy kod:** gałąź tworzenia default configu (config.json jest w repo), zależności
  `querystring`/`zlib`.
- **Jakość komunikatów:** wulgaryzmy w `throw new Error("Jprdl, to miała być tablica ...")`.
  W CLI-hobby to styl autora; w pluginie serwerowym — nie do przyjęcia, bo trafia do logów aplikacji.
- Szyfrowanie/bezpieczeństwo: hasło nie jest nigdzie zapisywane (plus), ale nie ma też wariantu
  env-var/tokenu, więc automatyzacja bez interactywnego TTY jest niemożliwa.

## Co z tym zrobić pod Jellyfin

Do ponownego użycia nadaje się **mapa API**, nie kod. Minimalny plan:

1. Przepisać klienta HTTP: cookie-jar + timeout + retry z backoffem + rate limit (1 req/s).
2. Parser na `cheerio`, czytać `value` i `ep_id` z `<li>`, nie liczyć numerów z kolejności.
3. Nazewnictwo pod Jellyfin: `<Tytuł>/Season 01/<Tytuł> - S01E01 - <tytuł odcinka>.mkv`,
   `-c` przy pobieraniu, rozszerzenie z faktycznego kontenera, traile do osobnego katalogu.
4. Jedna funkcja uruchamiająca yt-dlp z propagacją kodu wyjścia.
5. Sesja zapisana na dysk (cookie jar) zamiast logowania przy każdym starcie.

## Podsumowanie liczbowe

| Metryka | Wartość |
|---|---|
| Pliki / linie | 1 / 519 |
| Testy | 0 |
| Zależności | 4 (2 nieużywane, 2 to atrapy modułów core) |
| Endpointy rozpracowane | 5 |
| Blokery dla Jellyfin | 4 (sesja, nazewnictwo, błędy, brak timeoutów) |
| Zweryfikowane na żywym HTML | regex odcinków 1194/1194, numeracja poprawna |
