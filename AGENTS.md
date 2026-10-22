# Wytyczne projektowe: Anime dla Jellyfin

## 1. Synchronizacja kodu (Dual-Tree Invariant)
Katalog `client/` jest źródłem prawdy dla logiki w Pythonie. Katalog `oa_anime/app/` jest kopią wykorzystywaną przez dodatek Home Assistant.
- Każda zmiana w plikach `client/*.py` lub `client/providers/*.py` **musi** zostać natychmiast skopiowana do odpowiadających plików w `oa_anime/app/` i `oa_anime/app/providers/`.
- Nigdy nie modyfikuj `oa_anime/app/` w oderwaniu od `client/`.

## 2. Środowisko i testy (Zero External Test Dependencies)
- Wszystkie testy jednostkowe znajdują się w katalogu `tests/` i korzystają wyłącznie ze standardowej biblioteki Pythona (`unittest`).
- Nie dodawaj zależności do `pytest`, frameworków webowych ani zewnętrznych bibliotek testowych.
- Uruchamianie testów:
  - Na maszynach Windows w środowisku sandbox: używaj `& "C:\Python313\python.exe" -m unittest discover -s tests -v` z flagą `BypassSandbox: true`.
  - Na Linuksie: `python3 -m unittest discover -s tests -v`.

## 3. Wzorce architektury i obsługi błędów
- **Hierarchia wyjątków:** Wszystkie błędy domenowe serwisów streamingowych (`OgladajAnimeError`, `CdaError`, `ShindenError`) muszą dziedziczyć po `ProviderError`.
- **Cykliczne importy:** `ProviderError` jest definiowany w `client/oa.py` i re-eksportowany w `client/providers/__init__.py`. Pozostałe moduły (`cda.py`, `shinden.py`) importują go bezpośrednio z `oa.py`.
- **Współbieżność w `streamd`:** Nigdy nie wykonuj operacji I/O ani `time.sleep()` wewnątrz sekcji krytycznej `threading.Lock`. Korzystaj z mechanizmu per-odcinek (`threading.Event`) w `LinkCache`.
- **Stan kolekcji:** Przed jakimkolwiek odczytem lub zapisem w `kolekcja.py` należy przeładowywać plik `collection.json` z dysku za pomocą `collection.load()`, aby nie nadpisać zmian wprowadzonych w tle przez proces `sync.py`.

## 4. Git na Windows
- Utrzymuj `git config core.fileMode false`, aby zapobiec fałszywym modyfikacjom uprawnień skryptów `.sh`.

## 5. Wersjonowanie dodatku Home Assistant
- Każda zmiana funkcjonalna, poprawka błędu lub aktualizacja kodu w `client/` / `oa_anime/` **musi** wiązać się z podbiciem wersji w `oa_anime/config.yaml` (`version: "X.Y.Z"`).
- Bez zmiany pola `version`, Home Assistant Supervisor nie wykrywa aktualizacji repozytorium i nie przebudowuje kontenera dodatku.
