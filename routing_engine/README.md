# Opti-Transit — Routing Engine (Graf Warstwowy CSR)

Moduł w C++20 realizujący budowę **Grafu Warstwowego Zależnego od Czasu** w płaskich strukturach **CSR (Compressed Sparse Row)** na podstawie rozkładu GTFS Static z bazy PostgreSQL (`opti_transit_pipeline`).

## Struktura katalogu

```text
routing_engine/
├── CMakeLists.txt
├── Dockerfile
├── include/opti/
│   ├── graph.hpp          # TransitGraph, Node, Edge, TransitEdgeMeta, TripConnection, DelaySnapshot
│   ├── gtfs_data.hpp      # Struktury wejściowe dla tabel GTFS
│   ├── pg_loader.hpp      # Konfiguracja i loader PostgreSQL (libpq)
│   └── graph_builder.hpp  # Interfejs GraphBuilder (5 etapów budowy + funkcje pomocnicze)
├── src/
│   ├── graph.cpp          # TransitGraph::validate() — walidacja spójności CSR
│   ├── pg_loader.cpp      # Transakcja REPEATABLE READ READ ONLY + strumieniowy odczyt gtfs_stop_times
│   ├── graph_builder.cpp  # Etapy A..E (Parter, PatternKey/Piętra, CSR połączeń, siatka WALK, finalny CSR)
│   └── main.cpp           # CLI ładujące graf z bazy i wypisujące statystyki
└── tests/
    └── test_graph_builder.cpp
```

## Budowanie lokalne (CMake + C++20)

```bash
cmake -S routing_engine -B routing_engine/build -DCMAKE_BUILD_TYPE=Release
cmake --build routing_engine/build -j
ctest --test-dir routing_engine/build --output-on-failure
```

## Uruchomienie przeciwko bazie `opti_transit_pipeline`

Zmienne środowiskowe (domyślne wartości zgodne z `data_pipeline/db.py`):
- `DB_HOST` (domyślnie `localhost`)
- `DB_PORT` (domyślnie `5432`)
- `DB_NAME` (domyślnie `opti_transit_pipeline`)
- `DB_USER` (domyślnie `postgres`)
- `DB_PASSWORD` (domyślnie `postgres`)

```bash
./routing_engine/build/opti_graph_builder_cli
```

Lub przez Docker (buduje obraz, uruchamia testy `ctest` i uruchamia CLI):

```bash
docker build -t opti-transit-routing ./routing_engine
docker run --rm --network host -e DB_HOST=localhost -e DB_NAME=opti_transit_pipeline opti-transit-routing
```
