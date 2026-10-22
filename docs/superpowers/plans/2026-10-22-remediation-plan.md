# Plan naprawczy błędów i antywzorców (Anime dla Jellyfin)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Usunięcie krytycznych defektów funkcjonalnych, błędów współbieżności, niespójności stanu oraz antywzorców projektowych w repozytorium „Anime dla Jellyfin”.

**Architecture:** Wprowadzenie wspólnej hierarchii wyjątków domenowych (`ProviderError`), bezblokadowego pobierania zasobów sieciowych w `LinkCache`, bezpiecznego wyboru jakości wideo z mechanizmem fallbacku, synchronizacji stanu pliku `collection.json` między procesami, naprawy instalatora `install.sh` oraz utworzenia zestawu testów jednostkowych w oparciu o wbudowany moduł `unittest`.

**Tech Stack:** Python 3.10+, standard library (`unittest`, `threading`, `json`, `re`, `http.server`), `requests`, Bash / Systemd, Docker / Home Assistant Add-on.

**Spec:** Wyniki audytu systematycznego debugowania z analizy repozytorium (sekcje Faza 1–4).

## Global Constraints

- Utrzymanie zerowej liczby nowych zależności zewnętrznych (wyłącznie Python standard library + `requests`).
- Zachowanie pełnej wstecznej kompatybilności z istniejącymi plikami `.strm` (format `/e/<id>` dla ogladajanime, `/e/cda-<id>` dla CDA, `/e/sh-<id>` dla Shinden).
- Testy oparte na standardowym module `unittest` (aby działały w każdym środowisku bez konieczności instalowania `pytest`).
- Kopiowanie i synchronizacja kodu z `client/` do `oa_anime/app/` przy każdej zmianie.

## Review Focus

- Żądanie niedostępnej jakości wideo (np. `--quality 720` na materiale 1080p) musi dokonać degradacji do najlepszej dostępnej jakości zamiast zwracać 404.
- Wielowątkowe zapytania do `streamd` nie mogą blokować się nawzajem podczas oczekiwania na wolne odpowiedzi z zewnętrznych portali (zwłaszcza 5-sekundowy delay Shindena).
- Zewnętrzny proces crona (`sync.py`) i panel WWW (`kolekcja.py`) nie mogą nadpisywać nawzajem swoich zmian w `collection.json`.
- Wyjątki rzucane przez dowolnego providera (w tym `ShindenError`) muszą być bezpiecznie przechwytywane jako błędy HTTP 502/CLI bez zrzucania nieobsłużonych tracebacków.
- Skrypt instalatora `install.sh` musi zainstalować wszystkie wymagane moduły, w tym podkatalog `providers/`.

---

### Task 1: Ujednolicenie hierarchii wyjątków i usunięcie luk w obsłudze błędów

**Files:**
- Modify: `client/providers/__init__.py:18-46`
- Modify: `client/oa.py:47-54`
- Modify: `client/providers/cda.py:51-54`
- Modify: `client/providers/shinden.py:58-61`
- Modify: `client/kolekcja.py:226-258`
- Modify: `client/cli.py:251-260`
- Modify: `client/streamd.py:144-152`
- Modify: `client/sync.py:65-70`
- Create: `tests/test_exceptions.py`

**Interfaces:**
- Produces: `class ProviderError(RuntimeError)` w `client/providers/__init__.py`
- Inherits: `class OgladajAnimeError(ProviderError)` w `client/oa.py`
- Inherits: `class CdaError(ProviderError)` w `client/providers/cda.py`
- Inherits: `class ShindenError(ProviderError)` w `client/providers/shinden.py`

- [ ] **Step 1: Napisz test weryfikujący wspólną hierarchię wyjątków**

```python
# tests/test_exceptions.py
import unittest
from client.providers import ProviderError, CdaError, ShindenError
from client.oa import OgladajAnimeError, AuthError

class TestExceptionHierarchy(unittest.TestCase):
    def test_provider_errors_inherit_from_base(self):
        self.assertTrue(issubclass(OgladajAnimeError, ProviderError))
        self.assertTrue(issubclass(CdaError, ProviderError))
        self.assertTrue(issubclass(ShindenError, ProviderError))
        self.assertTrue(issubclass(AuthError, OgladajAnimeError))

    def test_polymorphic_catch(self):
        try:
            raise ShindenError("błąd shindena")
        except ProviderError as e:
            self.assertEqual(str(e), "błąd shindena")
```

- [ ] **Step 2: Uruchom test, aby potwierdzić niepowodzenie**

Uruchom: `python -m unittest tests/test_exceptions.py -v`
Oczekiwany wynik: FAIL (brak `ProviderError`).

- [ ] **Step 3: Zaimplementuj `ProviderError` i zaktualizuj klasy wyjątków**

1. W `client/providers/__init__.py` zdefiniuj:
   ```python
   class ProviderError(RuntimeError):
       """Bazowy wyjątek dla wszystkich błędów providerów treści."""
       pass
   ```
2. Zaktualizuj dziedziczenie w `oa.py`, `cda.py` i `shinden.py`.
3. Zastąp wyliczanie `(OgladajAnimeError, CdaError)` i `(AuthError, OgladajAnimeError, CdaError)` wspólnym `(ProviderError, AuthError)` w `kolekcja.py`, `cli.py`, `streamd.py` i `sync.py`. W `sync.py` dodaj także obsługę `OSError`.

- [ ] **Step 4: Uruchom test, aby potwierdzić powodzenie**

Uruchom: `python -m unittest tests/test_exceptions.py -v`
Oczekiwany wynik: PASS.

- [ ] **Step 5: Zacommituj zmiany**

```bash
git add client/ tests/test_exceptions.py
git commit -m "fix: introduce ProviderError base class and unify exception handling"
```

---

### Task 2: Bezpieczny wybór jakości z mechanizmem degradacji (Fallback) i poprawki CDA

**Files:**
- Modify: `client/oa.py:365-374`
- Modify: `client/providers/cda.py:282-300`
- Modify: `client/providers/cda.py:457-489`
- Create: `tests/test_quality.py`

**Interfaces:**
- Produces: `pick_best(links: Iterable[Link], preferred: int | None = None) -> Link | None` (z fallbackiem do najbliższej jakości zamiast `None`)
- Produces: `video_id_from_url(value: str) -> str | None` (case-insensitive)
- Produces: `Cda.links(vid: str) -> list[Link]` (zbiera wszystkie rozdzielczości lub respektuje `self.quality`)

- [ ] **Step 1: Napisz testy dla `pick_best`, `video_id_from_url` i linków CDA**

```python
# tests/test_quality.py
import unittest
from client.oa import Link, pick_best
from client.providers.cda import video_id_from_url

class TestQualityAndCda(unittest.TestCase):
    def test_pick_best_exact_match(self):
        links = [Link("1080p", 1080, "http://1080"), Link("720p", 720, "http://720")]
        self.assertEqual(pick_best(links, 720).res, 720)

    def test_pick_best_fallback_when_preferred_missing(self):
        links = [Link("1080p", 1080, "http://1080"), Link("480p", 480, "http://480")]
        # Użytkownik chce 720p, nie ma go -> wybierz najbliższy / najwyższy dostępny (1080p)
        best = pick_best(links, 720)
        self.assertIsNotNone(best)
        self.assertEqual(best.res, 1080)

    def test_video_id_case_insensitive(self):
        self.assertEqual(video_id_from_url("https://www.cda.pl/video/284660ABC"), "284660abc")
        self.assertEqual(video_id_from_url("https://ebd.cda.pl/620x368/ABCDEF12"), "abcdef12")
```

- [ ] **Step 2: Uruchom test, aby potwierdzić niepowodzenie**

Uruchom: `python -m unittest tests/test_quality.py -v`
Oczekiwany wynik: FAIL (fallback zwraca `None`, case-insensitive zwraca `None`).

- [ ] **Step 3: Zaimplementuj ulepszony `pick_best`, `video_id_from_url` i zbieranie jakości w `cda.py`**

1. W `client/oa.py` zaktualizuj `pick_best`:
   ```python
   def pick_best(links: Iterable[Link], preferred: int | None = None) -> Link | None:
       candidates = sorted(list(links), key=lambda l: l.res, reverse=True)
       if not candidates:
           return None
       if not preferred:
           return candidates[0]
       for l in candidates:
           if l.res == preferred:
               return l
       # Fallback: preferuj najwyższą dostępną <= preferred, inaczej najwyższą w ogóle
       lower_or_equal = [l for l in candidates if l.res <= preferred and l.res > 0]
       if lower_or_equal:
           return lower_or_equal[0]
       return candidates[0]
   ```
2. W `client/providers/cda.py` zaktualizuj `video_id_from_url`, dodając flagę `re.I` i `.lower()`.
3. W `client/providers/cda.py` w metodzie `links()` zbieraj wszystkie dostępne jakości do `out` (lub priorytetyzuj `self.quality`, jeśli ustawiono), zamiast przerywać natychmiast pętlę po pierwszym linku.

- [ ] **Step 4: Uruchom test, aby potwierdzić powodzenie**

Uruchom: `python -m unittest tests/test_quality.py -v`
Oczekiwany wynik: PASS.

- [ ] **Step 5: Zacommituj zmiany**

```bash
git add client/oa.py client/providers/cda.py tests/test_quality.py
git commit -m "fix: add quality fallback, fix CDA video_id regex and multi-quality extraction"
```

---

### Task 3: Optymalizacja współbieżności i usunięcie blokowania wątków w `LinkCache`

**Files:**
- Modify: `client/streamd.py:62-96`
- Create: `tests/test_cache.py`

**Interfaces:**
- Produces: `LinkCache.get(ep_id: str) -> str | None` (pobieranie sieciowe poza globalną blokadą; blokowanie tylko zapisu do pamięci lub blokada per-klucz)

- [ ] **Step 1: Napisz test wielowątkowy sprawdzający brak wzajemnego blokowania różnych odcinków**

```python
# tests/test_cache.py
import time
import threading
import unittest
from client.streamd import LinkCache
from client.oa import Link

class DummySource:
    def __init__(self):
        self.call_log = []

    def links(self, ep_id: str):
        self.call_log.append(ep_id)
        if ep_id == "slow":
            time.sleep(0.3)
        return [Link("1080p", 1080, f"http://video/{ep_id}")]

class TestLinkCacheConcurrency(unittest.TestCase):
    def test_concurrent_requests_dont_starve(self):
        source = DummySource()
        cache = LinkCache(source)

        def fetch_slow():
            cache.get("slow")

        def fetch_fast():
            time.sleep(0.05)
            start = time.monotonic()
            cache.get("fast")
            self.fast_duration = time.monotonic() - start

        t1 = threading.Thread(target=fetch_slow)
        t2 = threading.Thread(target=fetch_fast)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Zapytanie 'fast' nie powinno czekać 0.3s na zakończenie 'slow'
        self.assertLess(self.fast_duration, 0.25)
```

- [ ] **Step 2: Uruchom test, aby potwierdzić niepowodzenie**

Uruchom: `python -m unittest tests/test_cache.py -v`
Oczekiwany wynik: FAIL (`fast_duration >= 0.25s` z powodu trzymania `self._lock` w trakcie `time.sleep`).

- [ ] **Step 3: Zaimplementuj bezblokadowy odczyt I/O w `LinkCache`**

Zreorganizuj `LinkCache`:
```python
class LinkCache:
    def __init__(self, source, quality: int | None = None) -> None:
        self.source = source
        self.quality = quality
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._fetching: dict[str, threading.Event] = {}

    def get(self, ep_id: str) -> str | None:
        with self._lock:
            hit = self._data.get(ep_id)
            if hit and hit[1] > time.time():
                return hit[0]
            # Stampede protection per-odcinek
            event = self._fetching.get(ep_id)
            if event:
                # Inny wątek już pobiera ten sam odcinek - poczekaj na niego
                pass
            else:
                event = threading.Event()
                self._fetching[ep_id] = event

        if event.is_set():
            with self._lock:
                hit = self._data.get(ep_id)
                return hit[0] if hit else None

        # Jeśli to my utworzyliśmy event, pobieramy poza zamkiem:
        if self._fetching.get(ep_id) is event and not event.is_set():
            try:
                links = self.source.links(ep_id)
                link = pick_best(links, self.quality)
                url = link.src if link else None
            finally:
                with self._lock:
                    if url:
                        self._data[ep_id] = (url, self._expiry(url))
                    else:
                        self._data.pop(ep_id, None)
                    event.set()
                    self._fetching.pop(ep_id, None)
            return url
        else:
            event.wait(timeout=30)
            with self._lock:
                hit = self._data.get(ep_id)
                return hit[0] if hit else None
```

- [ ] **Step 4: Uruchom test, aby potwierdzić powodzenie**

Uruchom: `python -m unittest tests/test_cache.py -v`
Oczekiwany wynik: PASS.

- [ ] **Step 5: Zacommituj zmiany**

```bash
git add client/streamd.py tests/test_cache.py
git commit -m "perf: prevent LinkCache thread starvation by releasing lock during network I/O"
```

---

### Task 4: Synchronizacja stanu kolekcji i poprawka interfejsu WWW

**Files:**
- Modify: `client/kolekcja.py:147-152`
- Modify: `client/kolekcja.py:228-288`
- Modify: `client/collection.py:24-38`
- Create: `tests/test_collection_sync.py`

**Interfaces:**
- Produces: `State.reload(self)` lub dynamiczne czytanie `collection.load(st.path)` w endpointach `kolekcja.py`
- Produces: poprawne wyświetlanie tagu Shinden w interfejsie JS

- [ ] **Step 1: Napisz test weryfikujący automatyczne odświeżanie kolekcji z dysku**

```python
# tests/test_collection_sync.py
import json
import tempfile
import unittest
from pathlib import Path
from client import collection

class TestCollectionSync(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp_dir.name) / "collection.json"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_atomic_save_and_reload(self):
        data = {"series": {"s1": {"title": "Series 1"}}}
        collection.save(self.path, data)
        loaded = collection.load(self.path)
        self.assertEqual(loaded["series"]["s1"]["title"], "Series 1")

        # Symulacja zewnętrznej zmiany przez sync.py
        data["series"]["s2"] = {"title": "Series 2"}
        collection.save(self.path, data)
        reloaded = collection.load(self.path)
        self.assertIn("s2", reloaded["series"])
```

- [ ] **Step 2: Zaimplementuj przeładowywanie stanu w `kolekcja.py`**

1. W `client/kolekcja.py` w klasie `State`:
   ```python
   def get_data(self) -> dict:
       with self.lock:
           self.data = collection.load(self.path)
           return self.data
   ```
2. W `do_GET` (`/api/collection`): zwracaj `st.get_data()["series"]`.
3. W `_add` oraz `do_POST` (`/api/remove`): wywołaj `st.get_data()` przed modyfikacją, by nie nadpisywać zmian wprowadzonych przez `sync.py`.
4. W pliku HTML/JS w `client/kolekcja.py:149` popraw etykietę:
   ```javascript
   const tagClass = s === 'cda' ? 'cda' : (s === 'shinden' ? 'shinden' : 'ok');
   const tagLabel = s === 'cda' ? 'CDA' : (s === 'shinden' ? 'Shinden' : 'ogladajanime');
   ```

- [ ] **Step 3: Uruchom test, aby potwierdzić powodzenie**

Uruchom: `python -m unittest tests/test_collection_sync.py -v`
Oczekiwany wynik: PASS.

- [ ] **Step 4: Zacommituj zmiany**

```bash
git add client/kolekcja.py client/collection.py tests/test_collection_sync.py
git commit -m "fix: reload collection state dynamically to prevent lost updates from sync"
```

---

### Task 5: Naprawa instalatora systemd i skryptu pakowania dodatku HA

**Files:**
- Modify: `client/deploy/install.sh:24-30`
- Modify: `oa_anime/package.sh`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `install.sh` kopiujący `client/providers/*.py` do `$DEST/client/providers/`
- Produces: zaktualizowany `oa_anime/app/` zsynchronizowany z `client/`

- [ ] **Step 1: Popraw instalator `client/deploy/install.sh`**

W `client/deploy/install.sh`:
```bash
install -d -m 755 "$DEST/client"
install -m 644 "$SRC"/*.py "$DEST/client/"
[ -f "$SRC/README.md" ] && install -m 644 "$SRC/README.md" "$DEST/client/README.md"

# providers/ to warstwa zrodel — niezbedna do dzialania
install -d -m 755 "$DEST/client/providers"
install -m 644 "$SRC"/providers/*.py "$DEST/client/providers/"
```

- [ ] **Step 2: Zaktualizuj pliki dodatku HA i paczkę archiwum**

1. Uruchom kopiowanie plików z `client/` do `oa_anime/app/`.
2. Zweryfikuj spójność skrótów SHA plików między `client/` a `oa_anime/app/`.

- [ ] **Step 3: Zacommituj zmiany**

```bash
git add client/deploy/install.sh oa_anime/
git commit -m "fix: install providers/ in systemd installer and update HA addon app"
```

---

### Task 6: Poprawki odporności downloadera HLS i numeracji odcinków

**Files:**
- Modify: `client/jellyfin.py:79-100`
- Modify: `client/providers/cda.py:70-80`
- Create: `tests/test_hls_and_naming.py`

**Interfaces:**
- Produces: `_hls_download` weryfikujące brak AES-128 na playliście wariantu
- Produces: `episode_number` ignorujące pojedyncze lata wydania (np. 1995) na końcu tytułów filmów

- [ ] **Step 1: Napisz test dla parsowania numerów odcinków z tytułów z rokiem**

```python
# tests/test_hls_and_naming.py
import unittest
from client.providers.cda import episode_number

class TestNamingHeuristics(unittest.TestCase):
    def test_labeled_episode_number(self):
        self.assertEqual(episode_number("Odcinek 12"), 12)
        self.assertEqual(episode_number("Super Hero odc. 05"), 5)
        self.assertEqual(episode_number("Dragon Ball E03"), 3)

    def test_year_at_end_not_treated_as_episode(self):
        # Tytuły filmów z rokiem nie powinny dawać odcinka 1995/2020
        self.assertIsNone(episode_number("Dragon Ball Z Film 1995"))
        self.assertIsNone(episode_number("Ghost in the Shell 1995"))
        self.assertIsNone(episode_number("Akira 1988"))
```

- [ ] **Step 2: Zaimplementuj poprawkę heurystyki i weryfikacji AES-128**

1. W `client/providers/cda.py`: W `episode_number` jeśli liczba z `RE_NUM_TRAILING` mieści się w zakresie typowych lat wydania (1950–2040) i nie jest poprzedzona prefiksem "odc/ep/e", odrzuć ją.
2. W `client/jellyfin.py`: Sprawdzanie `#EXT-X-KEY:METHOD=AES-128` wykonaj po pobraniu `variant_url` na rzeczywistej playliście mediów.

- [ ] **Step 3: Uruchom test, aby potwierdzić powodzenie**

Uruchom: `python -m unittest tests/test_hls_and_naming.py -v`
Oczekiwany wynik: PASS.

- [ ] **Step 4: Zacommituj zmiany**

```bash
git add client/jellyfin.py client/providers/cda.py tests/test_hls_and_naming.py
git commit -m "fix: prevent year from matching as episode number and check AES-128 on media playlist"
```

---

### Task 7: Uruchomienie pełnego zestawu testów regresyjnych

- [ ] **Step 1: Uruchom wszystkie testy w projekcie**

Uruchom: `python -m unittest discover -s tests -v`
Oczekiwany wynik: Wszystkie testy (z zadań 1, 2, 3, 4, 6) przechodzą z wynikiem OK.

- [ ] **Step 2: Końcowa weryfikacja czystości repozytorium**

Uruchom: `git status` i upewnij się, że nie pozostały żadne niespójne lub nieśledzone pliki tymczasowe.
