#pragma once

#include <cstdint>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

namespace opti::routing {

inline constexpr uint32_t INVALID_IDX = std::numeric_limits<uint32_t>::max();

enum class EdgeType : uint8_t {
    BOARD = 0,          // Parter -> Piętro
    ALIGHT = 1,         // Piętro -> Parter
    WALK = 2,           // Parter -> Parter
    BOARDED_TRANSIT = 3 // Piętro(k) -> Piętro(k+1)
};

enum ModeBit : uint8_t {
    MODE_TRAM = 1 << 0,  // GTFS route_type 0
    MODE_METRO = 1 << 1, // GTFS route_type 1
    MODE_RAIL = 1 << 2,  // GTFS route_type 2
    MODE_BUS = 1 << 3    // GTFS route_type 3 (lub domyślny)
};

struct Node {
    double lat = 0.0;
    double lon = 0.0;
    uint32_t stop_idx = 0;               // Indeks fizycznego przystanku (0..num_stops-1)
    uint32_t pattern_idx = INVALID_IDX;  // INVALID_IDX = Parter, >= 0 = Piętro
    uint8_t mode_bit = 0;                // Maska środka transportu dla Piętra
    uint16_t alight_sec = 0;             // Stały koszt wyjścia z pojazdu na Parter
};

struct Edge {
    uint32_t target_node = 0;
    EdgeType type = EdgeType::WALK;
    // WALK: czas przejścia w sekundach
    // BOARDED_TRANSIT: indeks w `transit_edges`
    // BOARD / ALIGHT: 0 (koszty z parametrów zapytania / Node::alight_sec)
    uint32_t payload = 0;
};

struct TransitEdgeMeta {
    uint32_t first_conn = 0;
    uint32_t last_conn = 0;
    uint32_t physical_segment_id = 0; // Wspólny dla linii na tym samym (from_stop, to_stop, mode_bit)
};

struct TripConnection {
    uint32_t trip_internal_id = 0;
    uint32_t service_idx = 0;
    uint32_t scheduled_dep = 0;          // Sekundy od północy (obsługuje >= 86400)
    uint32_t scheduled_arr = 0;
    uint32_t next_conn_idx = INVALID_IDX; // Kolejny odcinek tego samego kursu w `connections`
    uint8_t can_board = 1;               // pickup_type != 1
    uint8_t can_alight = 1;              // drop_off_type != 1
};

struct PhysicalSegment {
    uint32_t from_stop_idx = 0;
    uint32_t to_stop_idx = 0;
    uint8_t mode_bit = 0;
};

// Równoległy wektor dynamicznych opóźnień (trzymany poza niemutowalnym TransitGraph)
struct ConnDelay {
    int16_t dep_delay_sec = 0;
    int16_t arr_delay_sec = 0;
};

struct DelaySnapshot {
    uint64_t graph_version = 0;
    std::vector<ConnDelay> values; // Rozmiar równy TransitGraph::connections.size()
};

struct ServiceCalendar {
    // Klucz: data YYYY-MM-DD -> posortowana lista aktywnych service_idx
    std::unordered_map<std::string, std::vector<uint32_t>> active_services_by_date;
};

struct TransitGraph {
    uint64_t version = 1;
    uint32_t num_stops = 0;    // Wierzchołki 0..num_stops-1 to zawsze Parter
    uint32_t num_patterns = 0; // Liczba unikalnych wzorców tras

    // Główny CSR grafu
    std::vector<Node> nodes;
    std::vector<uint32_t> first_edge; // Rozmiar: nodes.size() + 1
    std::vector<Edge> edges;

    // Rozkład jazdy pod krawędziami BOARDED_TRANSIT
    std::vector<TransitEdgeMeta> transit_edges;
    std::vector<TripConnection> connections;

    // Płaski CSR kursów (pod propagację opóźnień z GPS/ML)
    std::vector<uint32_t> trip_first_ref;    // Rozmiar: trip_ids.size() + 1
    std::vector<uint32_t> trip_conn_indices; // Indeksy do `connections` w kolejności trasy

    // Słowniki i metadane
    std::vector<PhysicalSegment> segments;
    std::vector<std::string> stop_ids;          // stop_idx -> GTFS stop_id
    std::vector<std::string> trip_ids;          // trip_internal_id -> GTFS trip_id
    std::vector<std::string> route_ids;         // route_idx -> GTFS route_id
    std::vector<std::string> route_short_names; // route_idx -> GTFS route_short_name
    std::vector<std::string> service_ids;       // service_idx -> GTFS service_id
    ServiceCalendar calendar;

    // Szybkie mapowanie odwrotne ID GTFS -> indeks wewnętrzny
    std::unordered_map<std::string, uint32_t> stop_id_to_idx;
    std::unordered_map<std::string, uint32_t> trip_id_to_internal;

    // Weryfikacja spójności struktur CSR oraz powiązań między tablicami
    bool validate(std::string* error_out = nullptr) const;
};

} // namespace opti::routing
