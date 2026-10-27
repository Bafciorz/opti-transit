# Opti-Transit — Data Pipeline

Moduł pobierania, przetwarzania i archiwizacji danych Warszawskiego Transportu Publicznego (WTP/ZTM) dla aplikacji wyszukiwania tras i przesiadek **Opti-Transit**.

Projekt w języku Python (>=3.14) zarządzany w całości przez nowoczesny menedżer pakietów **`uv`**.

---

## 🏗 Struktura katalogu `data_pipeline`

```
data_pipeline/
├── .env                  # Lokalne zmienne środowiskowe (hasła, host, port) - ignorowane przez Git
├── .env.example          # Wzorzec konfiguracji środowiskowej
├── .gitignore            # Ignorowane pliki (.env, .venv, *.parquet, *.tmp)
├── DATA_TASKS.md         # Plan realizacji i szczegółowy status tasków
├── DB_TABLES.md          # Kompletna dokumentacja tabel, kolumn, typów i specyfiki danych ZTM/WTP
├── README.md             # Dokumentacja modułu
├── pyproject.toml        # Zależności projektu uv (psycopg, httpx, aiohttp, rich, protobuf itp.)
├── uv.lock               # Blokada wersji zależności uv
├── db.py                 # Moduł połączeń z bazą PostgreSQL (pula połączeń sync/async, kursory, config, run_async)
├── runner.py             # Główny proces orchestratora: współbieżny collector, static sync, archiver, monitor
├── cli.py                # Narzędzie CLI (uv run data-pipeline: inspect, run, collect, load-gtfs, archive, query, verify)
├── logging_config.py     # Moduł logowania (konsola + rotowany logs/pipeline.log) i metryk PipelineMonitor
├── collector.py          # Pętla asynchroniczna collectora pozycji pojazdów na żywo (co 15s)
├── archiver.py           # Archiwizator PostgreSQL -> Parquet ZSTD z bezpiecznym DELETE oraz silnik DuckDB Reader
├── sql/
│   └── schema.sql        # Schemat bazy (tabele GTFS static + GTFS realtime + indeksy)
├── downloaders/
│   ├── gtfs_static.py    # Downloader warsaw.zip do folderu data/ z detekcją nowych wersji
│   └── gtfs_realtime.py  # Asynchroniczny downloader vehicles.pb z deduplikacją timestampów
├── loaders/
│   └── gtfs_static.py    # Bulk-loader GTFS do PostgreSQL w trybie Zero-Downtime (Blue-Green Swap)
├── writers/
│   └── vehicle_positions.py # Błyskawiczny writer pozycji do tabeli vehicle_positions przez COPY protocol (< 35ms)
├── data/                 # Katalog danych: warsaw.zip, archive/ (*.parquet) - ignorowane przez Git
├── logs/                 # Rotowane pliki logów (pipeline.log) - ignorowane przez Git
├── scripts/
│   └── init_db.py        # Skrypt tworzenia bazy danych i aplikowania schematu tabel
└── tests/
    ├── conftest.py       # Fixtury pytest (połączenia PG, event loop Windows, mocki ZIP i protobuf)
    ├── test_db.py        # Testy modułu połączeń PostgreSQL (sync/async pula, kursory, config)
    ├── test_realtime.py  # Testy GTFS-RT downloadera, parsowania Protobuf, deduplikacji i linii
    ├── test_writer.py    # Testy zapisu PostgreSQL COPY protocol (sync i async)
    ├── test_static_gtfs.py # Testy downloadera/loadera GTFS static, metadanych i konfiguracji tabel
    ├── test_archiver.py  # Testy archiwizacji Parquet ZSTD, bezpiecznego DELETE i DuckDB
    ├── test_logging_monitor.py # Testy konfiguracji logowania i metryk PipelineMonitor
    ├── test_runner_cli.py # Testy PipelineRunner, VehicleCollector i parsera CLI
    ├── verify_gtfs.py    # Skrypt diagnostyczny danych statycznych (rekordy, UTF-8, routing JOIN)
    ├── verify_realtime.py # Skrypt diagnostyczny pozycji realtime (granice, top linie, rozmiar)
    └── verify_archive.py # Skrypt weryfikacji archiwizacji Parquet (ZSTD, DuckDB, bezpieczny DELETE)
```

---

## ⚙️ Konfiguracja środowiska (`.env`)

Przed pierwszym uruchomieniem skopiuj plik `.env.example` do `.env` i dostosuj parametry:

```ini
# Baza danych PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=opti_transit
DB_USER=postgres
DB_PASSWORD=twoje_haslo

# URL do danych statycznych i realtime
GTFS_STATIC_URL=https://mkuran.pl/gtfs/warsaw.zip
GTFS_REALTIME_VEHICLES_URL=https://mkuran.pl/gtfs/warsaw/vehicles.pb

# Interwał collectora realtime (w sekundach)
COLLECTOR_INTERVAL_SECONDS=15

# Archiwizacja
ARCHIVE_RETENTION_HOURS=24
PARQUET_OUTPUT_DIR=data_pipeline/data/archive
```

---

## 🚀 Narzędzia i polecenia

Wszystkie polecenia uruchamia się będąc w folderze `data_pipeline/` za pomocą `uv run`:

### 1. Inicjalizacja bazy danych (`scripts/init_db.py`)
Automatycznie sprawdza, czy baza `opti_transit` istnieje (tworzy ją w razie potrzeby), po czym aplikuje definicje tabel i indeksów z `sql/schema.sql`:

```bash
# Inicjalizacja / aktualizacja tabel i indeksów:
uv run python scripts/init_db.py

# Tylko sprawdzenie stanu tabel i indeksów (bez modyfikacji):
uv run python scripts/init_db.py --check-only

# Całkowity reset i ponowne utworzenie tabel:
uv run python scripts/init_db.py --drop-all
```

---

### 2. Pobieranie rozkładów jazdy GTFS Static (`downloaders/gtfs_static.py`)
Pobiera plik `warsaw.zip` (106 MB) bezpośrednio do katalogu `data_pipeline/data/`:
- **Detekcja wersji:** Przed pobraniem sprawdza nagłówki HTTP (`Content-Length`, `ETag`, `Last-Modified`) oraz odczytuje `feed_info.txt` z pliku lokalnego i tabeli `feed_metadata`.
- **Oszczędność transferu:** Jeśli wersja w bazie jest aktualna, pomija pobieranie w ułamku sekundy.
- **Bezpieczeństwo:** Pobiera do pliku tymczasowego `.zip.tmp` i podmienia plik docelowy atomowo, co zapobiega uszkodzeniu archiwum przy przerwaniu transferu.

```bash
# Standardowe sprawdzenie i pobranie jeśli jest nowa wersja:
uv run python downloaders/gtfs_static.py

# Wymuszenie ponownego pobrania:
uv run python downloaders/gtfs_static.py --force
```

---

### 3. Ładowanie rozkładów do bazy — Zero-Downtime Blue-Green Swap (`loaders/gtfs_static.py`)
Strumieniowo ładuje wszystkie 7 relacji GTFS bezpośrednio z wnętrza archiwum ZIP za pomocą binarnego protokołu PostgreSQL `COPY FROM STDIN` (bez rozpakowywania plików na dysk).

#### 🛡 Mechanizm Blue-Green Table Swap:
1. Dane są najpierw strumieniowane do izolowanych tabel `_staging` (np. `gtfs_stop_times_staging`).
2. **Główne tabele produkcyjne pozostają w 100% odblokowane** — użytkownicy i backend Spring mogą bez przeszkód szukać tras w trakcie trwania ładowania.
3. Po załadowaniu wszystkich danych następuje atomowa podmiana tabel w **< 400 ms**:
   ```sql
   ALTER TABLE gtfs_stop_times RENAME TO gtfs_stop_times_old;
   ALTER TABLE gtfs_stop_times_staging RENAME TO gtfs_stop_times;
   DROP TABLE gtfs_stop_times_old CASCADE;
   ```
4. Indeksy są automatycznie przywracane do ich kanonicznych nazw (`idx_...`).

```bash
# Standardowe załadowanie (jeśli feed_version w bazie jest starszy):
uv run python loaders/gtfs_static.py

# Wymuszenie ponownego załadowania:
uv run python loaders/gtfs_static.py --force
```

---

### 4. Weryfikacja danych statycznych (`scripts/verify_gtfs.py`)
Weryfikuje stan bazy po migracji, sprawdzając:
1. Aktualne metadane feedu z `feed_metadata` (wersja, wydawca, timestamp).
2. Liczbę wierszy we wszystkich 7 tabelach GTFS (~9.86 mln).
3. Poprawność kodowania polskich znaków diakrytycznych UTF-8 (np. *Świętokrzyska*, *Al. Jana Pawła II*).
4. Relacje między tabelami (przykładowy testowy JOIN `trips` + `routes` + `stop_times` + `stops` dla metra M1).

```bash
uv run python scripts/verify_gtfs.py
```

---

### 5. Zbieranie pozycji pojazdów na żywo (`collector.py`)
Główna pętla asynchroniczna (`asyncio` + `aiohttp`), która cyklicznie co 15 sekund pobiera pozycje ~1900 aktywnych pojazdów (autobusy, tramwaje, metro M1/M2, SKM) z `vehicles.pb` i zapisuje je do PostgreSQL.

- **Wydajność:** Wykorzystuje protokół PostgreSQL `COPY` ([`writers/vehicle_positions.py`](writers/vehicle_positions.py)) — zapis ~2000 pojazdów trwa zaledwie **~30 ms**.
- **Deduplikacja:** Sprawdza `feed.header.timestamp` — jeśli feed na serwerze nie zmienił się od poprzedniej iteracji, pomija duplikat i nie obciąża bazy.
- **Odporność na błędy:** Automatyczne wznawianie połączenia z bazą danych w razie chwilowych przerw sieciowych.
- **Graceful Shutdown:** Bezpieczne zamykanie połączeń po naciśnięciu `Ctrl+C` (SIGINT/SIGTERM) z wyświetleniem tabeli podsumowującej.

```bash
# Uruchomienie ciągłego zbierania co 15 sekund:
uv run python collector.py

# Uruchomienie z własnym interwałem (np. co 10 sekund):
uv run python collector.py --interval 10

# Uruchomienie testowe na określoną liczbę iteracji (np. 3 iteracje):
uv run python collector.py --max-iterations 3
```

---

### 6. Weryfikacja danych realtime (`scripts/verify_realtime.py`)
Sprawdza stan tabeli `vehicle_positions`:
- Liczba zapisanych pozycji, unikalnych pojazdów i obsługiwanych linii.
- Walidacja granic geograficznych (sprawdzenie czy lat/lon mieszczą się w aglomeracji warszawskiej: Lat ~52, Lon ~21).
- Podgląd ostatnich zarejestrowanych pojazdów (linia, numer boczny, prędkość, kąt jazdy, timestamp GPS).
- Zestawienie Top 10 linii o największej liczbie pozycji.
- Aktualny rozmiar tabeli i indeksów w PostgreSQL oraz szacunek dobowego przyrostu danych.

```bash
uv run python scripts/verify_realtime.py
```

---

### 7. Archiwizacja do Parquet (`archiver.py`)
Przenosi dane starsze niż zadany próg (domyślnie 24 godziny) z PostgreSQL do partycjonowanych plików Parquet z kompresją Zstandard (ZSTD).

- **Partycjonowanie:** `data/archive/date=YYYY-MM-DD/hour=HH.parquet` (zgodne z konwencją Hive / DuckDB / PyArrow).
- **Weryfikacja integralności:** Zapis następuje do pliku tymczasowego `.parquet.tmp`. Przed usunięciem danych z PostgreSQL skrypt odczytuje metadane pliku Parquet i weryfikuje zgodność liczby wierszy (`meta.num_rows == len(rows)`).
- **Bezpieczny DELETE:** Dopiero po pomyślnym zapisie i weryfikacji Parquet, zarchiwizowane rekordy są usuwane z PostgreSQL w ramach transakcji atomowej. W razie jakiegokolwiek błędu baza pozostaje nienaruszona.
- **Kompresja ZSTD:** Redukuje objętość danych o ponad **85-90%** (zamiast ~3.3 GB/dobę w bazie, pliki Parquet zajmują ~150-250 MB).
- **Idempotentność:** Jeśli plik dla danej godziny już istnieje, nowe rekordy są scalane i deduplikowane po `(vehicle_id, time)`.

```bash
# Standardowa archiwizacja starszych niż 24h:
uv run python archiver.py archive

# Archiwizacja starszych niż N godzin (np. 4 godziny):
uv run python archiver.py archive --older-than-hours 4

# Symulacja (Dry Run) — sprawdza ile rekordów zostałoby zarchiwizowanych bez zmian na dysku/w bazie:
uv run python archiver.py archive --older-than-hours 1 --dry-run

# Eksport do Parquet z zachowaniem danych w PostgreSQL (bez usuwania):
uv run python archiver.py archive --no-delete

# Statystyki zarchiwizowanych plików:
uv run python archiver.py stats
```

---

### 8. Odpytywanie zarchiwizowanych danych przez DuckDB (`archiver.py query`)
Umożliwia analityczne odpytywanie skompresowanych plików Parquet bez konieczności ich dekompresji na dysk:

- **Partition Pruning:** Silnik DuckDB skanuje wyłącznie katalogi odpowiadające zadanemu zakresowi dat.
- **Filter & Projection Pushdown:** Filtry po linii (`route_id`), pojeździe (`vehicle_id`) oraz rzutowanie kolumn są przekazywane bezpośrednio do warstwy dekodowania bloków Parquet.
- **Interfejs programistyczny:**
  ```python
  from archiver import query_archived_positions

  # Pobierz pozycje linii 503 z danego dnia jako pandas DataFrame:
  df = query_archived_positions(date_from="2026-10-27", route_id="503")

  # Pobierz jako PyArrow Table z wybranymi kolumnami:
  table = query_archived_positions(
      route_id=["180", "503"],
      columns=["time", "vehicle_id", "route_id", "latitude", "longitude"],
      as_arrow=True,
  )
  ```
- **CLI query:**
  ```bash
  # Podgląd ostatnich zarchiwizowanych pozycji dla wybranej linii:
  uv run python archiver.py query --route-id 503 --limit 10

  # Filtrowanie po zakresie dat i pojeździe:
  uv run python archiver.py query --date-from 2026-10-27 --vehicle-id V/503/10
  ```

---

### 9. Weryfikacja modułu archiwizacji (`scripts/verify_archive.py`)
Kompletny test end-to-end całego procesu archiwizacji:
1. Wstawia syntetyczną paczkę 500 rekordów sprzed 48 godzin do `vehicle_positions`.
2. Uruchamia `archive_old_positions()` do izolowanego katalogu testowego.
3. Weryfikuje strukturę partycji `date=.../hour=...parquet`, schemat 10 kolumn, metadane wierszy oraz kompresję ZSTD.
4. Potwierdza usunięcie rekordów z tabeli PostgreSQL.
5. Testuje zapytania DuckDB (zakres dat, filtry linii, projection pushdown, format PyArrow).
6. Sprząta dane i pliki testowe.

```bash
uv run python scripts/verify_archive.py
```

---

### 10. Główny proces — Unified Runner (`runner.py` / `uv run runner`)
Orchestrator łączący wszystkie zadania w pojedynczym procesie asynchronicznym (`asyncio`):

- **Realtime Vehicle Collector:** Cykliczne odpytywanie `vehicles.pb` co 15s i zapis do PostgreSQL.
- **Static GTFS Sync:** Sprawdzanie dostępności nowego rozkładu jazdy codziennie (domyślnie o **03:00** czasu warszawskiego) w osobnym wątku (`asyncio.to_thread`) i bezprzerwowa podmiana (Zero-Downtime Blue-Green Swap).
- **Parquet Archiver:** Automatyczna archiwizacja danych starszych niż 24h codziennie (domyślnie o **04:00**) do skompresowanych plików Parquet (ZSTD) i czyszczenie PostgreSQL.
- **Health & Metrics Monitor:** Cykliczne raportowanie stanu bazy, zebranych pozycji i uptime'u co 5 minut.
- **Graceful Shutdown:** Bezpieczne zatrzymanie pętli po naciśnięciu `Ctrl+C` z wyświetleniem tabeli podsumowującej sesję.

```bash
# Uruchomienie pełnego pipeline'u:
uv run runner
# lub:
uv run python runner.py

# Konfiguracja interwałów i godzin:
uv run runner --collector-interval 15 --static-sync-hour 3 --archive-hour 4

# Uruchomienie testowe (pojedyncza iteracja collectora):
uv run runner --run-once
```

---

### 11. Logowanie i monitoring (`logging_config.py`)
Jednolity system logowania dla wszystkich komponentów pipeline'u:

- **Format:** `[TIMESTAMP] [MODULE] [LEVEL] Message` (np. `[2026-10-27 21:53:09] [opti_transit] [INFO] Inserted 1020 vehicle positions in 198.8 ms`).
- **Wyjście podwójne:** Kolorowy output na konsolę oraz rotowany plik `logs/pipeline.log` (10 MB, do 5 plików archiwalnych, kodowanie UTF-8).
- **Klasa `PipelineMonitor`:** Zbiera metryki w czasie rzeczywistym i cyklicznie raportuje m.in. uptime, liczbę wierszy, błędy, wielkość tabeli `vehicle_positions` i całkowity rozmiar bazy PostgreSQL.

---

### 12. Uniwersalne narzędzie CLI (`cli.py` / `uv run data-pipeline`)
Pojedynczy interfejs wiersza poleceń integrujący wszystkie operacje:

```bash
# Pomoc i spis dostępnych komend:
uv run data-pipeline --help

# Szybki podgląd stanu systemu, połączenia z bazą i rozmiaru tabel:
uv run data-pipeline inspect

# Szczegółowe statystyki bazy i archiwum Parquet:
uv run data-pipeline stats

# Uruchomienie pełnego orchestratora (runner):
uv run data-pipeline run

# Uruchomienie tylko collectora realtime:
uv run data-pipeline collect --interval 15

# Załadowanie rozkładów GTFS Static:
uv run data-pipeline load-gtfs

# Archiwizacja starych danych do Parquet:
uv run data-pipeline archive --older-than-hours 24

# Szybkie zapytanie DuckDB do archiwum Parquet:
uv run data-pipeline query --route-id 503 --limit 10

# Uruchomienie skryptów diagnostycznych (wszystkie lub gtfs, realtime, archive):
uv run data-pipeline verify --suite all

# Uruchomienie pełnego zestawu testów jednostkowych i integracyjnych pytest:
uv run pytest
# lub przez CLI:
uv run data-pipeline verify --suite unit
```

---

## 📊 Tabele i wolumen danych w PostgreSQL

Po załadowaniu feedu GTFS Static oraz uruchomieniu collectora w bazie znajdują się:

| Tabela PostgreSQL | Plik źródłowy | Liczba wierszy | Opis |
|---|---|---:|---|
| `gtfs_agency` | `agency.txt` | 1 | Przewoźnicy (ZTM Warszawa) |
| `gtfs_routes` | `routes.txt` | 351 | Linie (autobusy, tramwaje, metro M1/M2, SKM, kolej) |
| `gtfs_stops` | `stops.txt` | 7,466 | Słupki przystankowe (współrzędne GPS, strefy biletowe, ulice) |
| `gtfs_trips` | `trips.txt` | 289,108 | Kursy w rozkładzie jazdy (warianty, kierunki, dostępność) |
| `gtfs_stop_times` | `stop_times.txt` | 8,233,724 | Przyjazdy i odjazdy na każdym przystanku w kursie |
| `gtfs_calendar_dates` | `calendar_dates.txt` | 93 | Kalendarz kursowania i wyjątki (święta, dni robocze) |
| `gtfs_shapes` | `shapes.txt` | 1,331,657 | Punkty geometrii GPS do rysowania dokładnego przebiegu tras |
| `feed_metadata` | *generowane* | 1 | Wersja feedu, czas załadowania i statystyki |
| `vehicle_positions` | `vehicles.pb` | ~10.9M / dobę | Szereg czasowy pozycji pojazdów na żywo (~1900 na tick co 15s) |

---

## 💡 Uruchamianie z głównego folderu projektu

Jeśli znajdujesz się w głównym katalogu `opti-transit/`, możesz uruchomić dowolne polecenie dodając flagę `--project data_pipeline`:

```bash
# Pełny pipeline w jednym poleceniu:
uv run --project data_pipeline runner

# Narzędzie CLI:
uv run --project data_pipeline data-pipeline inspect
uv run --project data_pipeline data-pipeline stats
uv run --project data_pipeline data-pipeline query --route-id 503 --limit 5
uv run --project data_pipeline data-pipeline verify --suite all
```
