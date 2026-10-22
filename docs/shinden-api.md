# Shinden.pl — rozpoznanie (trzecie źródło)

Sprawdzone na żywo, bez logowania, zwykłym `requests`.

## Wniosek

Shinden **nie hostuje wideo** — jest katalogiem odcinków i agregatorem odtwarzaczy.
Realnie odtwarzalny jest tylko **CDA** (najczęstszy serwis); resztę stanowią
odtwarzacze JS-owe, szyfrowane albo na martwych domenach.

Pokrycie zależy od tytułu, nie od serwisu: „Jujutsu Kaisen" sezon 1 → 3/3 próbki
gra przez CDA, sezon 2 → 0/3 (tylko Filemoon, Vidara, Vk, Yourupload).

## Dlaczego wcześniej wyglądało na zablokowane

`curl` dostawał `307 → ?r307=1 → HTTP 467` (własny anti-bot Shindena).
**Python `requests` przechodzi bez problemu** — blokada dotyczy fingerprintu
curl-a, nie samego ruchu. Nie trzeba więc ani przeglądarki, ani solvera.

## Endpointy

### Wyszukiwanie

```
GET https://shinden.pl/titles?search=<fraza>
```

Uwaga: `/search?query=` to tylko strona z „popularnymi" — zwraca to samo dla
każdej frazy. Prawdziwa wyszukiwarka to `/titles`.

### Odcinki

```
GET https://shinden.pl/series/<id>-<slug>/all-episodes      (albo /episodes)
```

Tabela `tbody.list-episode-checkboxes`, wiersze `<tr data-episode-no="24">`,
tytuł w `.ep-title`, adres w `a.button.active[href]` = `/episode/<seria>/view/<ep_id>`.
Seria jest wypisana **od najnowszego odcinka** — trzeba odwrócić.

### Playery odcinka

Strona odcinka zawiera listę odtwarzaczy w HTML jako JSON `data-episode`:

```json
{"online_id":"1397671","player":"Cda","max_res":"1080p","lang_audio":"jp",
 "lang_subs":"pl","subs_author":"...","added":"...","source":"..."}
```

### Adres odtwarzacza (XHR, gościnny)

```
GET https://api4.shinden.pl/xhr/<online_id>/player_load?auth=<token>
    ... odczekać 5 sekund ...
GET https://api4.shinden.pl/xhr/<online_id>/player_show?auth=<token>&width=0&height=-1
```

Zwraca HTML z `<iframe>` do serwisu z wideo.

Trzy pułapki, z których każda daje `403 Błędne wejście`:

1. **5 sekund przerwy jest obowiązkowe** — Shinden przygotowuje odtwarzacz
   asynchronicznie; 2 s to za mało.
2. **Token `auth` bierz z `_Storage.basic` na stronie odcinka** i podawaj go
   w `params=` **niezakodowanego** — podanie `%3D` daje `%253D` i odrzucenie.
3. **Sesja musi najpierw odwiedzić stronę odcinka** (ciasteczko `sess_shinden`).

## Serwisy i ich realna dostępność

| Serwis | Adres z iframe | Odtwarzalne |
|---|---|---|
| **Cda** | `ebd.cda.pl/<w>x<h>/<id>` | ✅ naszym providerem CDA (HLS `.m3u8`, `200` dla dowolnego UA) |
| Gdrive | `drive.google.com/file/d/<id>/preview` | ⚠️ wymaga obsługi `confirm` |
| Yourupload | `//www.yourupload.com/embed/<id>` | ⚠️ zwykle plik wprost, do sprawdzenia |
| Vk | `vk.com/video_ext.php?...` | ❌ brak adresów w HTML, własne API |
| Filemoon | `bysesukior.com/e/<id>` | ❌ 1,6 kB, odtwarzacz w JS |
| Vidara | `gsabigo.com/e/<id>` | ❌ domena nie odpowiada |
| Mega | `mega.nz/embed/#!<id>!<klucz>` | ❌ wymaga deszyfrowania po stronie klienta |
| Crunchyroll / Youtube | — | ❌ DRM / inny charakter |
