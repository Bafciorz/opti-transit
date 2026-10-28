# Opti-Transit — Dokumentacja Bazy Danych i Schematu Tabel (`DB_TABLES.md`)

Niniejszy dokument stanowi kompletny przewodnik po schemacie relacyjnej bazy danych **PostgreSQL** (`opti_transit`), formatach danych, specyfikacji kolumn, relacjach oraz specyfice warszawskiego transportu publicznego (**ZTM / WTP**).

---
## 1. Tabela `gtfs_agency` (Przewoźnicy)

Zawiera informacje o organizatorze i emitencie rozkładu jazdy transportu miejskiego w aglomeracji warszawskiej.

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `agency_id` | `TEXT` | NIE | **PRIMARY KEY** | Unikalny identyfikator przewoźnika | `'0'` |
| `agency_name` | `TEXT` | NIE | - | Oficjalna pełna nazwa organizatora | `'Warszawski Transport Publiczny'` |
| `agency_url` | `TEXT` | TAK | - | Strona internetowa przewoźnika | `'https://wtp.waw.pl'` |
| `agency_timezone` | `TEXT` | TAK | - | Strefa czasowa obsługiwanego obszaru | `'Europe/Warsaw'` |
| `agency_lang` | `TEXT` | TAK | - | Kod języka komunikatów | `'pl'` |
| `agency_phone` | `TEXT` | TAK | - | Infolinia miejska | `'+48 22 19 115'` |

---

## 2. Tabela `gtfs_routes` (Linie komunikacyjne)

Definiuje wszystkie linie autobusowe, tramwajowe, linie metra oraz pociągi Szybkiej Kolei Miejskiej (SKM).

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `route_id` | `TEXT` | NIE | **PRIMARY KEY** | Unikalny identyfikator linii | `'1'`, `'180'`, `'503'`, `'M1'`, `'S1'` |
| `agency_id` | `TEXT` | TAK | `FK -> gtfs_agency` | Identyfikator agencji | `'0'` |
| `route_short_name`| `TEXT` | TAK | - | Numer/symbol handlowy linii widoczny dla pasażera | `'1'`, `'180'`, `'M2'`, `'N44'`, `'L24'` |
| `route_long_name` | `TEXT` | TAK | - | Nazwa kierunkowa trasy (krańce główne) | `'Annopol — Banacha'`, `'Wilanów — Chomiczówka'` |
| `route_type` | `INT` | NIE | - | Kod typu środka transportu wg standardu GTFS | `0` (tramwaj), `1` (metro), `2` (kolej), `3` (autobus) |
| `route_color` | `TEXT` | TAK | - | Kolor linii w formacie HEX (bez `#`) | `'B60000'` (czerwony tramwaj), `'880077'`, `'00A0E2'` |
| `route_text_color`| `TEXT` | TAK | - | Kolor tekstu na tle koloru linii | `'FFFFFF'` (biały), `'000000'` (czarny) |

### 💡 Wartości `route_type` w Warszawie:
- **`0` (Tramwaj):** 29 linii (np. 1, 9, 17, 24, 33). Kolor zazwyczaj `#B60000`.
- **`1` (Metro):** 2 linie (`M1` Kabaty-Młociny - granatowy, `M2` Bemowo-Bródno - błękitny).
- **`2` (Kolej aglomeracyjna SKM):** 5 linii (`S1`, `S2`, `S3`, `S4`, `S40`).
- **`3` (Autobusy):** 315 linii (dzienne `1xx`-`2xx`, przyspieszone `5xx`, strefowe podmiejskie `7xx`-`8xx`, nocne `Nxx`, lokalne `Lxx`).

---

## 3. Tabela `gtfs_stops` (Przystanki, perony i wejścia)

Zawiera fizyczne punkty zatrzymań (słupki przystankowe, perony stacji kolejowych i metra oraz wejścia do stacji).

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `stop_id` | `TEXT` | NIE | **PRIMARY KEY** | Unikalny 6-cyfrowy identyfikator słupka ZTM | `'100101'`, `'700201'` |
| `stop_name` | `TEXT` | NIE | `idx_gtfs_stops_name` | Nazwa zespołu przystankowego | `'Kijowska'`, `'Centrum'`, `'Metro Świętokrzyska'` |
| `stop_code` | `TEXT` | TAK | - | Numer słupka w zespole (zazwyczaj 2 cyfry) | `'01'`, `'02'`, `'03'` |
| `platform_code` | `TEXT` | TAK | - | Oznaczenie peronu (dla stacji kolejowych) | `'1'`, `'2'` lub `NULL` |
| `stop_lat` | `DOUBLE` | NIE | - | Szerokość geograficzna WGS84 | `52.248455` |
| `stop_lon` | `DOUBLE` | NIE | - | Długość geograficzna WGS84 | `21.044827` |
| `location_type` | `INT` | TAK | - | Typ obiektu w infrastrukturze przystankowej | `0` (słupek), `1` (stacja), `2` (wejście do metra) |
| `parent_station` | `TEXT` | TAK | `FK -> gtfs_stops` | Identyfikator nadrzędnej stacji węzłowej | `'station_M1_Centrum'` lub `NULL` |
| `wheelchair_boarding` | `INT` | TAK | - | Dostępność dla wózków inwalidzkich | `1` (dostępny), `2` (niedostępny), `0` (brak danych) |
| `zone_id` | `TEXT` | TAK | - | Strefa biletowa ZTM Warszawa | `'1'`, `'2'`, `'1+2'` |
| `stop_name_stem` | `TEXT` | TAK | - | Rdzeń nazwy przystanku (bez sufiksów) | `'Kijowska'`, `'Dworzec Centralny'` |
| `town_name` | `TEXT` | TAK | - | Miejscowość, na terenie której leży słupek | `'Warszawa'`, `'Piaseczno'`, `'Legionowo'`, `'Pruszków'` |
| `street_name` | `TEXT` | TAK | - | Nazwa ulicy przy której fizycznie stoi słupek | `'Targowa'`, `'Marszałkowska'`, `'Al. Jerozolimskie'` |

### 💡 Specyfika identyfikatora `stop_id` w ZTM Warszawa:
Warszawski `stop_id` to **zawsze 6 cyfr**:
- **Pierwsze 4 cyfry:** identyfikator *zespołu przystankowego* (np. `1001` = zespół "Kijowska").
- **Ostatnie 2 cyfry:** numer *słupka/stanowiska* w zespole (np. `01`, `02`, `03` itd.). Słupki tramwajowe w Warszawie często mają numery `03`, `04`, a autobusowe `01`, `02` lub wyższe.

### 💡 Strefy biletowe `zone_id`:
- `'1'`: 1. strefa biletowa (m.st. Warszawa) — 5 257 słupków.
- `'2'`: 2. strefa biletowa (gminy ościenne aglomeracji) — 1 995 słupków.
- `'1+2'`: Przystanki graniczne na granicy stref — 214 słupków.

---

## 4. Tabela `gtfs_trips` (Kursy w rozkładzie jazdy)

Reprezentuje konkretny wariant przejazdu danej linii (od pętli początkowej do końcowej).

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `trip_id` | `TEXT` | NIE | **PRIMARY KEY** | Unikalny identyfikator kursu w Warszawie | `'2026-10-27:102:PcS:09:1426'` |
| `route_id` | `TEXT` | NIE | `idx_gtfs_trips_route_id` | Identyfikator linii | `'102'`, `'180'`, `'503'` |
| `service_id` | `TEXT` | NIE | `idx_gtfs_trips_service_id` | Identyfikator kalendarza kursowania | `'2026-10-27:PcS'` |
| `shape_id` | `TEXT` | TAK | `FK -> gtfs_shapes` | Identyfikator geometrii trasy kursu | `'3:22'`, `'0:0'` |
| `trip_short_name`| `TEXT` | TAK | - | Skrócona nazwa kursu (zazwyczaj pusta) | `NULL` |
| `trip_headsign` | `TEXT` | TAK | - | Napis na wyświetlaczu czołowym pojazdu | `'Metro Stadion Narodowy'`, `'PKP Olszynka Grochowska'` |
| `direction_id` | `INT` | TAK | - | Kierunek kursu (`0` lub `1`) | `0` (tam), `1` (powrót) |
| `exceptional` | `INT` | TAK | - | Wskaźnik nietypowego przebiegu trasy | `0` (standardowa trasa), `1` (objazd / skrót) |
| `wheelchair_accessible` | `INT` | TAK | - | Dostępność taboru dla niepełnosprawnych | `1` (niskopodłogowy), `2` (wysokopodłogowy) |
| `block_id` | `TEXT` | TAK | - | Identyfikator brygady dziennej pojazdu | `'2026-10-27:838394'` |
| `block_short_name`| `TEXT` | TAK | - | Numer brygady taborowej | `'09'`, `'2'`, `'11'` |
| `variant_code` | `TEXT` | TAK | - | Wewnętrzny kod wariantu trasy ZTM | `'TO-MSN'`, `'TP-OLS'` |
| `fleet_type` | `TEXT` | TAK | - | Wymagany typ taboru do obsługi kursu | `'G-np18m'` (przegubowy 18m), `'T-np'` (tramwaj niski) |

### 💡 Anatomia warszawskiego `trip_id`:
Identyfikator kursu w WTP składa się z 5 segmentów rozdzielonych dwukropkami:
```
{DATA}:{LINIA}:{TYP_KALENDARZA}:{BRYGADA}:{PLANOWANY_START}
2026-10-27 : 102 : PcS : 09 : 1426
```
- `2026-10-27`: Dzień kursowania.
- `102`: Numer linii.
- `PcS`: Typ dnia (`PcS` = dzień powszedni, `SbS` = sobota, `DsS` = niedziela/święto).
- `09`: Numer brygady.
- `1426`: Planowany czas odjazdu z pętli początkowej (godzina 14:26).

---

## 5. Tabela `gtfs_stop_times` (Czasy odjazdów i przyjazdów)

Największa tabela w bazie (~8.23 mln wierszy). Zawiera dokładną kolejność przystanków i planowany czas dla każdego kursu.

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `trip_id` | `TEXT` | NIE | **PRIMARY KEY (1/2)** | Identyfikator kursu | `'2026-10-27:102:PcS:09:1426'` |
| `stop_sequence` | `INT` | NIE | **PRIMARY KEY (2/2)** | Kolejność przystanku na trasie kursu (od 0) | `0`, `1`, `2`, `3` ... |
| `stop_id` | `TEXT` | NIE | `idx_gtfs_stop_times_stop_id` | Identyfikator słupka przystankowego | `'210804'`, `'100101'` |
| `arrival_time` | `TEXT` | NIE | - | Planowany czas przyjazdu (`HH:MM:SS`) | `'14:26:00'`, `'25:12:00'` |
| `departure_time`| `TEXT` | NIE | - | Planowany czas odjazdu (`HH:MM:SS`) | `'14:26:00'`, `'25:12:00'` |
| `pickup_type` | `INT` | TAK | - | Typ wsiadania na tym przystanku | `0` (stały), `1` (brak wsiadania), `3` (na żądanie) |
| `drop_off_type` | `INT` | TAK | - | Typ wysiadania na tym przystanku | `0` (stały), `1` (brak wysiadania), `3` (na żądanie) |
| `shape_dist_traveled` | `DOUBLE` | TAK | - | Dystans od startu trasy w kilometrach | `0.0`, `0.340602`, `14.8521` |

### ⚠️ Bardzo ważna specyfika GTFS: Czasy powyżej 24:00 (`arrival_time` / `departure_time`):
Zgodnie ze światowym standardem GTFS, kursy nocne rozpoczynające się po północy, ale należące do doby eksploatacyjnej danego dnia, mają godziny większe niż `24:00:00`.
- W bazie `opti_transit` zakres czasów wynosi od `'00:00:00'` do `'29:39:00'`.
- Np. `'25:15:00'` oznacza godzinę **01:15 w nocy** dnia następnego!
- Z tego powodu kolumny te są typu `TEXT`, a nie `TIME` (typ `TIME` w PostgreSQL akceptuje wyłącznie wartości `00:00:00` - `24:00:00`).

### 💡 Wartości `pickup_type` / `drop_off_type`:
- `0`: Przystanek stały — autobus/tramwaj zatrzymuje się zawsze (6 080 332 wpisów).
- `1`: Brak możliwości wsiadania/wysiadania — np. przystanek dla wysiadających na pętli końcowej (81 049 wpisów).
- `3`: Przystanek **"na żądanie" (NŻ)** — pasażer musi zasygnalizować chęć wysiadania przyciskiem STOP lub machnąć ręką na przystanku (2 072 343 wpisów).

---

## 6. Tabela `gtfs_calendar_dates` (Wyjątki kalendarza kursowania)

Określa w które dni kalendarzowe dany zestaw kursów (`service_id`) obowiązuje.

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `service_id` | `TEXT` | NIE | **PRIMARY KEY (1/2)** | Identyfikator kalendarza | `'2026-10-27:PcS'` |
| `date` | `DATE` | NIE | **PRIMARY KEY (2/2)**, `INDEX` | Konkretna data w kalendarzu | `'2026-10-27'` |
| `exception_type`| `INT` | NIE | `INDEX` | Typ wyjątku | `1` (kursuje w tym dniu), `2` (nie kursuje) |

---

## 7. Tabela `gtfs_shapes` (Przebieg geometrii tras)

Zawiera precyzyjną geometrię łamanych GPS (polilinii) do rysowania dokładnego przebiegu ulicznego tras na mapie.

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `shape_id` | `TEXT` | NIE | **PRIMARY KEY (1/2)** | Identyfikator wariantu geometrii trasy | `'0:0'`, `'3:22'` |
| `shape_pt_sequence` | `INT` | NIE | **PRIMARY KEY (2/2)** | Numer kolejny punktu na łamanej trasy (od 0) | `0`, `1`, `2`, `3` ... |
| `shape_pt_lat` | `DOUBLE`| NIE | - | Szerokość geograficzna punktu WGS84 | `52.255199` |
| `shape_pt_lon` | `DOUBLE`| NIE | - | Długość geograficzna punktu WGS84 | `21.053946` |
| `shape_dist_traveled` | `DOUBLE` | TAK | - | Narastający dystans w kilometrach od startu | `0.0`, `0.009738`, `0.015772` |

---

## 8. Tabela `vehicle_positions` (Bieżące pozycje pojazdów na żywo)

Szereg czasowy gromadzący aktualne pozycje GPS autobusów i tramwajów pobierane co 15 sekund z oficjalnego API m.st. Warszawy (`dane.um.warszawa.pl`).

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `time` | `TIMESTAMPTZ` | NIE | `INDEX (time DESC)` | Znacznik czasu pozycji GPS wyemitowany przez pojazd | `'2026-10-28 11:30:16+01'` |
| `fetched_at` | `TIMESTAMPTZ` | NIE | - | Czas pobrania ramki przez collector (`DEFAULT NOW()`) | `'2026-10-28 11:30:20+01'` |
| `vehicle_id` | `TEXT` | NIE | `idx_..._vehicle_time` | Unikalny identyfikator pojazdu w formacie `V/{Linia}/{NrBoczny}` | `'V/190/1001'`, `'V/9/2001'` |
| `route_id` | `TEXT` | TAK | `idx_..._route_time` | Numer linii autobusowej lub tramwajowej | `'190'`, `'509'`, `'9'`, `'24'`, `'M1'` |
| `brigade` | `TEXT` | TAK | `idx_..._route_brigade`| Numer brygady w rozkładzie miejskim | `'1'`, `'03'`, `'7'`, `'M1'` |
| `latitude` | `DOUBLE` | NIE | - | Aktualna szerokość geograficzna GPS | `52.229712` |
| `longitude`| `DOUBLE` | NIE | - | Aktualna długość geograficzna GPS | `21.012234` |
| `vehicle_label` | `TEXT` | TAK | - | Numer taborowy/boczny pojazdu | `'1001'`, `'2001'`, `'9339'` |

> [!NOTE]
> Zgodnie ze specyfikacją oficjalnego API Warszawy, parametry pochodne (azymut `bearing` oraz prędkość `speed`) nie są nadawane przez GPS i są kalkulowane w warstwie analitycznej w locie z kolejnych próbek GPS pojazdu.

### 💡 Indeksy na tabeli `vehicle_positions`:
1. `idx_vehicle_positions_vehicle_time ON (vehicle_id, time DESC)` — błyskawiczne odtworzenie ścieżki i historii pojedynczego pojazdu.
2. `idx_vehicle_positions_route_time ON (route_id, time DESC)` — odpytanie o wszystkie pojazdy danej linii w danym oknie czasowym.
3. `idx_vehicle_positions_route_brigade ON (route_id, brigade)` — filtrowanie pojazdów wg linii i przypisanej brygady.
4. `idx_vehicle_positions_time ON (time DESC)` — wykorzystywany przez archiwizator Parquet do usuwania danych starszych niż 24h.

---

## 9. Tabela `feed_metadata` (Metadane załadowanych rozkładów)

Zawiera rejestr wersji pobranych paczek `warsaw.zip`, datę załadowania oraz liczbę rekordów w poszczególnych tabelach.

### Struktura kolumn:
| Kolumna | Typ SQL | Nullable | Klucz / Indeks | Opis biznesowy | Przykładowa wartość |
|---|---|---|---|---|---|
| `id` | `SERIAL` | NIE | **PRIMARY KEY** | Unikalny identyfikator wpisu | `1`, `2` |
| `feed_version` | `TEXT` | NIE | - | Wersja rozkładu odczytana z `feed_info.txt` | `'2026-10-27 00:15:31'` |
| `feed_publisher_name` | `TEXT` | TAK | - | Nazwa wydawcy rozkładu | `'Mikołaj Kuranowski / ZTM'` |
| `loaded_at` | `TIMESTAMPTZ`| NIE | - | Dokładna data i czas importu do PostgreSQL | `'2026-10-27 16:36:03+01'` |
| `routes_count` | `INT` | TAK | - | Liczba załadowanych linii | `351` |
| `stops_count` | `INT` | TAK | - | Liczba załadowanych przystanków | `7466` |
| `trips_count` | `INT` | TAK | - | Liczba załadowanych kursów | `289108` |
| `stop_times_count` | `INT` | TAK | - | Liczba załadowanych czasów odjazdów | `8233724` |

---

## 10. Archiwum Parquet (`data/archive/`)

Dane telemetryczne z `vehicle_positions` starsze niż 24 godziny są codziennie archiwizowane do formatu Apache Parquet z kompresją Zstandard (ZSTD).

### Struktura katalogów i partycjonowanie (Hive format):
```
data/archive/
├── date=2026-10-26/
│   ├── hour=00.parquet
│   ├── hour=01.parquet
│   └── ...
└── date=2026-10-27/
    ├── hour=16.parquet   (119.5 KB, 5 782 wierszy)
    └── ...
```

### Schemat PyArrow w plikach Parquet:
```python
pa.schema([
    ("time", pa.timestamp("us", tz="UTC")),
    ("fetched_at", pa.timestamp("us", tz="UTC")),
    ("vehicle_id", pa.string()),
    ("route_id", pa.string()),
    ("brigade", pa.string()),
    ("latitude", pa.float64()),
    ("longitude", pa.float64()),
    ("vehicle_label", pa.string()),
])
```

---

## 🔗 Przykłady zapytań SQL łączących dane

### 1. Odpytanie o aktualne pozycje pojazdów danej linii i brygady:
```sql
SELECT 
    vp.time AS czas_gps,
    vp.route_id AS linia,
    vp.brigade AS brygada,
    vp.vehicle_label AS nr_boczny,
    vp.latitude,
    vp.longitude
FROM vehicle_positions vp
WHERE vp.route_id = '190'
ORDER BY vp.time DESC
LIMIT 10;
```

### 2. Rozkład odjazdów z konkretnego przystanku (np. Metro Świętokrzyska):
```sql
SELECT 
    r.route_short_name AS linia,
    t.trip_headsign AS kierunek,
    st.departure_time AS planowany_odjazd,
    s.stop_name,
    s.stop_code AS slupek
FROM gtfs_stop_times st
JOIN gtfs_trips t ON st.trip_id = t.trip_id
JOIN gtfs_routes r ON t.route_id = r.route_id
JOIN gtfs_stops s ON st.stop_id = s.stop_id
WHERE s.stop_name = 'Centrum'
  AND st.departure_time >= '15:00:00'
ORDER BY st.departure_time ASC
LIMIT 20;
```
