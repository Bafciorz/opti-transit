# Opti-Transit — Data Pipeline

Moduł pobierania, przetwarzania i archiwizacji danych Warszawskiego Transportu Publicznego (WTP/ZTM) dla aplikacji wyszukiwania tras Opti-Transit.

Zarządzany za pomocą **`uv`**.

---

## 🏗 Struktura folderu

```
data_pipeline/
├── .env                  # Lokalne zmienne środowiskowe (hasła, host, port) - ignorowane przez git
├── .env.example          # Wzorzec konfiguracji
├── .gitignore            # Ignorowane pliki (.env, .venv, *.parquet, downloads/)
├── DATA_TASKS.md         # Plan realizacji i status tasków
├── README.md             # Dokumentacja modułu
├── pyproject.toml        # Zależności projektu (psycopg, rich, protobuf, itp.)
├── db.py                 # Moduł połączeń z bazą PostgreSQL (connection pool, kursory, config)
├── sql/
│   └── schema.sql        # Schemat bazy (tabele GTFS static + GTFS realtime + indeksy)
└── scripts/
    └── init_db.py        # Skrypt tworzenia bazy danych i aplikowania schematu
```

---

## ⚙️ Konfiguracja (.env)

Skopiuj plik `.env.example` do `.env` (jeśli jeszcze nie istnieje) i dostosuj parametry:

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=opti_transit
DB_USER=postgres
DB_PASSWORD=twoje_haslo
```

---

## 🚀 Uruchomienie

### 1. Inicjalizacja bazy danych:
Tworzy bazę `opti_transit` (o ile nie istnieje), aplikuje schemat tabel i zakłada indeksy:

```bash
# Z katalogu data_pipeline:
uv run python scripts/init_db.py

# Tylko sprawdzenie stanu bazy:
uv run python scripts/init_db.py --check-only

# Reset i ponowne zaaplikowanie tabel:
uv run python scripts/init_db.py --drop-all
```

Możesz także uruchamiać z głównego katalogu repozytorium:
```bash
uv run --project data_pipeline python data_pipeline/scripts/init_db.py
```
