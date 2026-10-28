-- =============================================================================
-- Opti-Transit: GTFS Static + GTFS Realtime Database Schema
-- Kompatybilny z PostgreSQL 15+ oraz TimescaleDB (po włączeniu rozszerzenia)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Agencje / przewoźnicy (ZTM, Tramwaje Warszawskie, MZA, Metro, SKM, KM)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_agency (
    agency_id TEXT PRIMARY KEY,
    agency_name TEXT NOT NULL,
    agency_url TEXT,
    agency_timezone TEXT,
    agency_lang TEXT,
    agency_phone TEXT
);

-- -----------------------------------------------------------------------------
-- 1. Linie komunikacyjne (routes.txt)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_routes (
    route_id TEXT PRIMARY KEY,
    agency_id TEXT,
    route_short_name TEXT,
    route_long_name TEXT,
    route_type INT NOT NULL,
    route_color TEXT,
    route_text_color TEXT
);

-- -----------------------------------------------------------------------------
-- 2. Przystanki (stops.txt)
-- Zawiera warszawskie rozszerzenia: town_name, street_name, stop_name_stem
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_stops (
    stop_id TEXT PRIMARY KEY,
    stop_name TEXT NOT NULL,
    stop_code TEXT,
    platform_code TEXT,
    stop_lat DOUBLE PRECISION NOT NULL,
    stop_lon DOUBLE PRECISION NOT NULL,
    location_type INT DEFAULT 0,
    parent_station TEXT,
    wheelchair_boarding INT DEFAULT 0,
    zone_id TEXT,
    stop_name_stem TEXT,
    town_name TEXT,
    street_name TEXT
);

-- -----------------------------------------------------------------------------
-- 3. Kursy (trips.txt)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    shape_id TEXT,
    trip_short_name TEXT,
    trip_headsign TEXT,
    direction_id INT,
    exceptional INT DEFAULT 0,
    wheelchair_accessible INT DEFAULT 0,
    block_id TEXT,
    block_short_name TEXT,
    variant_code TEXT,
    fleet_type TEXT
);

-- -----------------------------------------------------------------------------
-- 4. Czasy na przystankach (stop_times.txt)
-- arrival_time i departure_time jako TEXT ze względu na specyfikację GTFS (godziny >= 24:00:00)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    trip_id TEXT NOT NULL,
    stop_sequence INT NOT NULL,
    stop_id TEXT NOT NULL,
    arrival_time TEXT NOT NULL,
    departure_time TEXT NOT NULL,
    pickup_type INT DEFAULT 0,
    drop_off_type INT DEFAULT 0,
    shape_dist_traveled DOUBLE PRECISION,
    PRIMARY KEY (trip_id, stop_sequence)
);

-- -----------------------------------------------------------------------------
-- 5. Kalendarz kursowania / wyjątki (calendar_dates.txt)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (
    service_id TEXT NOT NULL,
    date DATE NOT NULL,
    exception_type INT NOT NULL,
    PRIMARY KEY (service_id, date)
);

-- -----------------------------------------------------------------------------
-- 6. Geometria przebiegu tras (shapes.txt)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gtfs_shapes (
    shape_id TEXT NOT NULL,
    shape_pt_sequence INT NOT NULL,
    shape_pt_lat DOUBLE PRECISION NOT NULL,
    shape_pt_lon DOUBLE PRECISION NOT NULL,
    shape_dist_traveled DOUBLE PRECISION,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

-- -----------------------------------------------------------------------------
-- 7. Pozycje pojazdów w czasie rzeczywistym (vehicles.pb)
-- Tabela szeregów czasowych — docelowo TimescaleDB hypertable na produkcji
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicle_positions (
    time TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    vehicle_id TEXT NOT NULL,
    route_id TEXT,
    brigade TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    vehicle_label TEXT
);

-- -----------------------------------------------------------------------------
-- 8. Metadane feedu GTFS (wersja pobranego warsaw.zip, statystyki)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feed_metadata (
    id SERIAL PRIMARY KEY,
    feed_version TEXT NOT NULL,
    feed_publisher_name TEXT,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    routes_count INT,
    stops_count INT,
    trips_count INT,
    stop_times_count INT
);

-- =============================================================================
-- INDEKSY
-- =============================================================================

-- vehicle_positions: wyszukiwanie historii danego pojazdu lub linii oraz archiwizacja po czasie
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_vehicle_time ON vehicle_positions (vehicle_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_route_time ON vehicle_positions (route_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_route_brigade ON vehicle_positions (route_id, brigade);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_time ON vehicle_positions (time DESC);

-- GTFS static: indeksy do szybkiego planowania podróży (routing)
CREATE INDEX IF NOT EXISTS idx_gtfs_trips_route_id ON gtfs_trips (route_id);
CREATE INDEX IF NOT EXISTS idx_gtfs_trips_service_id ON gtfs_trips (service_id);
CREATE INDEX IF NOT EXISTS idx_gtfs_stop_times_stop_id ON gtfs_stop_times (stop_id);
CREATE INDEX IF NOT EXISTS idx_gtfs_stops_name ON gtfs_stops (stop_name);
CREATE INDEX IF NOT EXISTS idx_gtfs_calendar_dates_date ON gtfs_calendar_dates (date, exception_type);
