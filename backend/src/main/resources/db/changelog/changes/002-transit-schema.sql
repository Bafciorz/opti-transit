--liquibase formatted sql
--changeset opti-transit:002-transit-schema

-- 1. Linie (tramwaje, autobusy, metro, kolej)
CREATE TABLE IF NOT EXISTS gtfs_routes (
    route_id TEXT PRIMARY KEY,
    agency_id TEXT,
    route_short_name TEXT NOT NULL,
    route_long_name TEXT,
    route_type INT NOT NULL,
    route_color TEXT DEFAULT '000000',
    route_text_color TEXT DEFAULT 'FFFFFF'
);

-- 2. Przystanki i perony/słupki
CREATE TABLE IF NOT EXISTS gtfs_stops (
    stop_id TEXT PRIMARY KEY,
    stop_name TEXT NOT NULL,
    stop_code TEXT,
    platform_code TEXT,
    stop_lat DOUBLE PRECISION NOT NULL,
    stop_lon DOUBLE PRECISION NOT NULL,
    town_name TEXT DEFAULT 'Warszawa',
    wheelchair_boarding INT DEFAULT 0
);

-- 3. Kursy linii
CREATE TABLE IF NOT EXISTS gtfs_trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES gtfs_routes(route_id),
    service_id TEXT NOT NULL,
    trip_headsign TEXT,
    direction_id INT,
    wheelchair_accessible INT DEFAULT 0
);

-- 4. Rozkład jazdy (czasy na przystankach)
CREATE TABLE IF NOT EXISTS gtfs_stop_times (
    trip_id TEXT NOT NULL REFERENCES gtfs_trips(trip_id) ON DELETE CASCADE,
    stop_sequence INT NOT NULL,
    stop_id TEXT NOT NULL REFERENCES gtfs_stops(stop_id),
    arrival_time TEXT NOT NULL,
    departure_time TEXT NOT NULL,
    PRIMARY KEY (trip_id, stop_sequence)
);

-- 5. Geometria przebiegu tras na mapie
CREATE TABLE IF NOT EXISTS gtfs_shapes (
    shape_id TEXT NOT NULL,
    shape_pt_sequence INT NOT NULL,
    shape_pt_lat DOUBLE PRECISION NOT NULL,
    shape_pt_lon DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

-- 6. Przesiadki piesze między przystankami
CREATE TABLE IF NOT EXISTS stop_transfers (
    from_stop_id TEXT NOT NULL REFERENCES gtfs_stops(stop_id),
    to_stop_id TEXT NOT NULL REFERENCES gtfs_stops(stop_id),
    min_transfer_time INT NOT NULL DEFAULT 120,
    distance_meters DOUBLE PRECISION,
    PRIMARY KEY (from_stop_id, to_stop_id)
);

-- 7. Pozycje pojazdów na żywo
CREATE TABLE IF NOT EXISTS vehicle_positions (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id TEXT NOT NULL,
    route_id TEXT,
    brigade TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    time TIMESTAMPTZ NOT NULL
);

-- 8. Metadane feedu GTFS
CREATE TABLE IF NOT EXISTS feed_metadata (
    id SERIAL PRIMARY KEY,
    feed_version TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stops_count INT,
    routes_count INT,
    trips_count INT
);

-- Indeksy pod szybkie wyszukiwanie i zapytania trasowe
CREATE INDEX IF NOT EXISTS idx_gtfs_stops_name ON gtfs_stops (stop_name);
CREATE INDEX IF NOT EXISTS idx_gtfs_stop_times_stop_id ON gtfs_stop_times (stop_id);
CREATE INDEX IF NOT EXISTS idx_gtfs_stop_times_departure ON gtfs_stop_times (departure_time);
CREATE INDEX IF NOT EXISTS idx_vehicle_positions_route ON vehicle_positions (route_id, time DESC);
