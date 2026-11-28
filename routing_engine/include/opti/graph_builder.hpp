#pragma once

#include "opti/graph.hpp"
#include "opti/gtfs_data.hpp"

#include <cstdint>

namespace opti::routing {

struct GraphBuildOptions {
    uint64_t version = 1;
    double max_walk_distance_meters = 350.0;
    double walk_speed_mps = 1.25;
    bool skip_walk_to_non_boarding_stops = true;
};

struct GraphBuildStats {
    uint32_t parter_nodes = 0;
    uint32_t floor_nodes = 0;
    uint32_t board_edges = 0;
    uint32_t alight_edges = 0;
    uint32_t walk_edges = 0;
    uint32_t transit_edges = 0;
    uint32_t connections = 0;
    uint32_t trips_loaded = 0;
    uint32_t trips_skipped = 0;
    uint32_t stop_times_skipped = 0;
    uint32_t physical_segments = 0;
};

class GraphBuilder {
public:
    // Parsuje czas GTFS ("HH:MM:SS", w tym wartości >= 24:00:00) na sekundy od północy
    static uint32_t parse_gtfs_time(const std::string& time_str);

    // Mapuje GTFS route_type na maskę bitową ModeBit
    static uint8_t map_route_type(int32_t route_type);

    // Domyślny koszt zejścia z pojazdu na Parter w sekundach
    static uint16_t alight_cost_for_mode(uint8_t mode_bit);

    // Dystans Haversine w metrach
    static double haversine_meters(double lat1, double lon1, double lat2, double lon2);

    // Buduje kompletny graf warstwowy CSR w 5 etapach (A..E)
    static TransitGraph build(
        const GtfsDataset& dataset,
        const GraphBuildOptions& options = {},
        GraphBuildStats* stats_out = nullptr
    );
};

} // namespace opti::routing
