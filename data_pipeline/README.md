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
├── README.md             # Dokumentacja modułu
├── pyproject.toml        # Zależności projektu uv (psycopg, httpx, aiohttp, rich, protobuf itp.)
├── uv.lock               # Blokada wersji zależności uv
├── db.py                 # Moduł połączeń z bazą PostgreSQL (pula połączeń sync/async, kursory, config, run_async)
├── collector.py          # Główna pętla asynchroniczna collectora pozycji pojazdów na żywo (co 15s)
├── sql/
│   └── schema.sql        # Schemat bazy (tabele GTFS static + GTFS realtime + indeksy)
├── downloaders/
│   ├── gtfs_static.py    # Downloader warsaw.zip do folderu data/ z detekcją nowych wersji
│   └── gtfs_realtime.py  # Asynchroniczny downloader vehicles.pb z deduplikacją timestampów
├── loaders/
│   └── gtfs_static.py    # Bulk-loader GTFS do PostgreSQL w trybie Zero-Downtime (Blue-Green Swap)
├── writers/
│   └── vehicle_positions.py # Błyskawiczny writer pozycji do tabeli vehicle_positions przez COPY protocol (< 35ms)
└── scripts/
    ├── init_db.py        # Skrypt tworzenia bazy danych i aplikowania schematu tabel
    ├── verify_gtfs.py    # Skrypt diagnostyczny danych statycznych (rekordy, kodowanie UTF-8, routing JOIN)
    └── verify_realtime.py # Skrypt diagnostyczny pozycji realtime (granice Warszawy, top linie, rozmiar bazy)
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
PARQUET_OUTPUT_DIR=data_pipeline/archive
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
Pobiera plik `warsaw.zip` (106 MB) bezpośrednio do głównego katalogu `data/`:
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
uv run --project data_pipeline python data_pipeline/collector.py
uv run --project data_pipeline python data_pipeline/scripts/verify_realtime.py
uv run --project data_pipeline python data_pipeline/scripts/verify_gtfs.py
uv run --project data_pipeline python data_pipeline/loaders/gtfs_static.py
```
