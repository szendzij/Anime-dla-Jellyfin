# CDA.pl — rozpoznanie (drugie źródło, bez Cloudflare)

Wszystko sprawdzone **anonimowo**, zwykłym `curl`/`requests` — bez przeglądarki, bez logowania.

## Dlaczego CDA

Shinden i anime-odcinki.pl/animezone.pl są za wyzwaniem JS (Shinden: własny anti-bot
`307 -> ?r307=1 -> HTTP 467`; pozostałe: Cloudflare „Just a moment"). Podszycie się pod TLS
przeglądarki (`curl_cffi`, 6 profili) daje 403 — challenge wymaga wykonania JS.

CDA nie ma wyzwania (Cloudflare tylko jako CDN) i udostępnia treść zwykłym klientom.

## Endpointy

### 1. Wyszukiwanie
```
GET https://www.cda.pl/info?key=<zapytanie>
```
HTML; wyłuskujemy `/video/<id>` oraz foldery `/<user>/folder/<id>`.
Jakość wyników dla tytułów anime jest średnia — to portal wideo, nie katalog anime.

### 2. Folder = seria (lista odcinków w HTML)
```
GET https://www.cda.pl/<user>/folder/<id>
```
Serwer renderuje: tytuł folderu, listę pozycji (`title="Super Mission 01"` + `/video/<id>`)
oraz podfoldery z licznikami `( 3 )`. Uwaga: treść bywa śmieciowa (przekierowania na Google
Drive, AMV, memy) — folder to pojemnik użytkownika, nie oficjalna seria.

### 3. Strona odcinka -> manifest
```
GET https://www.cda.pl/video/<id>/vfilm
Cookie: cda.player=html5
```
W HTML jest JSON odtwarzacza:
- `"manifest"` — HLS `.m3u8` **albo** DASH `.mpd` (zależnie od filmu), z tokenem czasowym
- `"qualities":{"360p":"vl","480p":"lq","720p":"sd","1080p":"hd","auto":"auto"}`
- `"ts"`, `"hash"`, `"hash2"`, `"premium"`, `"durationFull"`

Manifest pobiera się bez dodatkowych nagłówków: `HTTP 200`, `application/dash+xml`
lub `application/vnd.apple.mpegurl`. Token wygasa po ~6 h (epoch w URL).

### 4. Bezpośredni plik (najlepsze dla Jellyfina)
```
POST https://www.cda.pl/video/<id>
Content-Type: application/json
X-Requested-With: XMLHttpRequest
Referer: https://www.cda.pl/video/<id>/vfilm
Cookie: cda.player=html5

{"jsonrpc":"2.0","method":"videoGetLink","id":2,
 "params":["<id>", "<kod_jakosci: vl|lq|sd|hd|auto>", <ts>, "<hash2>", {}]}
```
Odpowiedź: `result.status == "ok"`, `result.resp` = URL (mp4 / mpd / m3u8).
Działa bez logowania.

### 5. JSON-RPC całego serwisu
```
POST https://www.cda.pl/api.php     (Zend JSON-RPC, opis: GET /api.php?smd)
```
SMD ogólny wymienia 30 usług, ale **nie** zawiera metod strony folderu
(`getFolderView` istnieje tylko w `cda.min.js`, serwer odpowiada `-32601 Method not found`).
Dlatego listę odcinków bierzemy z HTML folderu, nie przez to API.

## Konsekwencje dla dodatku

- Ten sam wzorzec co ogladajanime: `.strm` -> redirector mintujący świeży link przy odtworzeniu.
- Dla CDA redirector może zwrócić **302 na MP4** (`videoGetLink`) — prostsze niż HLS i daje
  natywne przewijanie w Jellyfinie.
- Zero przeglądarki: `requests` + parser HTML. Nie obciąża HA.
- Jakość: część materiałów HD wymaga konta Premium; anonimowo sprawdzone warianty
  360p/480p/720p na testowych filmach działały (`premium: false`).
