#include "opti/graph_builder.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <numbers>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace opti::routing {

namespace {

inline void hash_combine(size_t& seed, size_t value) noexcept {
    seed ^= value + 0x9e3779b97f4a7c15ULL + (seed << 6) + (seed >> 2);
}

struct PatternStop {
    uint32_t stop_idx = 0;
    bool can_board = true;
    bool can_alight = true;

    bool operator==(const PatternStop&) const = default;
};

struct PatternKey {
    uint32_t route_idx = 0;
    int32_t direction_id = -1;
    std::vector<PatternStop> stops;

    bool operator==(const PatternKey&) const = default;
};

struct PatternKeyHash {
    size_t operator()(const PatternKey& key) const noexcept {
        size_t seed = std::hash<uint32_t>{}(key.route_idx);
        hash_combine(seed, std::hash<int32_t>{}(key.direction_id));
        hash_combine(seed, key.stops.size());
        for (const auto& s : key.stops) {
            hash_combine(seed, std::hash<uint32_t>{}(s.stop_idx));
            size_t flags = (s.can_board ? 1ULL : 0ULL) | (s.can_alight ? 2ULL : 0ULL);
            hash_combine(seed, flags);
        }
        return seed;
    }
};

struct SegmentKey {
    uint32_t from_stop_idx = 0;
    uint32_t to_stop_idx = 0;
    uint8_t mode_bit = 0;

    bool operator==(const SegmentKey&) const = default;
};

struct SegmentKeyHash {
    size_t operator()(const SegmentKey& key) const noexcept {
        size_t seed = std::hash<uint32_t>{}(key.from_stop_idx);
        hash_combine(seed, std::hash<uint32_t>{}(key.to_stop_idx));
        hash_combine(seed, std::hash<uint8_t>{}(key.mode_bit));
        return seed;
    }
};

struct GridCell {
    int32_t gx = 0;
    int32_t gy = 0;

    bool operator==(const GridCell&) const = default;
};

struct GridCellHash {
    size_t operator()(const GridCell& cell) const noexcept {
        size_t seed = std::hash<int32_t>{}(cell.gx);
        hash_combine(seed, std::hash<int32_t>{}(cell.gy));
        return seed;
    }
};

struct TempRouteMeta {
    uint32_t route_idx = 0;
    uint8_t mode_bit = MODE_BUS;
};

struct TempTripMeta {
    uint32_t route_idx = 0;
    uint32_t service_idx = 0;
    int32_t direction_id = -1;
    uint8_t mode_bit = MODE_BUS;
};

struct TempConn {
    uint32_t trip_internal_id = 0;
    uint32_t seq_k = 0;
    uint32_t service_idx = 0;
    uint32_t scheduled_dep = 0;
    uint32_t scheduled_arr = 0;
    uint8_t can_board = 1;
    uint8_t can_alight = 1;
};

struct ParsedTripStop {
    int32_t stop_sequence = 0;
    uint32_t stop_idx = 0;
    uint32_t arrival_sec = 0;
    uint32_t departure_sec = 0;
    bool can_board = true;
    bool can_alight = true;
};

} // namespace

uint32_t GraphBuilder::parse_gtfs_time(const std::string& time_str) {
    int h = 0;
    int m = 0;
    int s = 0;
    if (std::sscanf(time_str.c_str(), "%d:%d:%d", &h, &m, &s) != 3) {
        throw std::invalid_argument("Invalid GTFS time format: " + time_str);
    }
    if (h < 0 || m < 0 || s < 0) {
        throw std::invalid_argument("Negative GTFS time component: " + time_str);
    }
    return static_cast<uint32_t>(h * 3600 + m * 60 + s);
}

uint8_t GraphBuilder::map_route_type(int32_t route_type) {
    switch (route_type) {
        case 0:
            return MODE_TRAM;
        case 1:
            return MODE_METRO;
        case 2:
            return MODE_RAIL;
        default:
            return MODE_BUS;
    }
}

uint16_t GraphBuilder::alight_cost_for_mode(uint8_t mode_bit) {
    if (mode_bit == MODE_METRO) {
        return 60;
    }
    if (mode_bit == MODE_RAIL) {
        return 45;
    }
    return 15;
}

double GraphBuilder::haversine_meters(double lat1, double lon1, double lat2, double lon2) {
    constexpr double kEarthRadiusMeters = 6371000.0;
    constexpr double kDegToRad = std::numbers::pi / 180.0;

    const double dlat = (lat2 - lat1) * kDegToRad;
    const double dlon = (lon2 - lon1) * kDegToRad;
    const double rlat1 = lat1 * kDegToRad;
    const double rlat2 = lat2 * kDegToRad;

    const double sin_dlat = std::sin(dlat * 0.5);
    const double sin_dlon = std::sin(dlon * 0.5);
    const double a = sin_dlat * sin_dlat + std::cos(rlat1) * std::cos(rlat2) * sin_dlon * sin_dlon;
    const double c = 2.0 * std::atan2(std::sqrt(a), std::sqrt(std::max(0.0, 1.0 - a)));
    return kEarthRadiusMeters * c;
}

TransitGraph GraphBuilder::build(
    const GtfsDataset& dataset,
    const GraphBuildOptions& options,
    GraphBuildStats* stats_out
) {
    TransitGraph graph;
    graph.version = options.version;

    GraphBuildStats stats;

    // -------------------------------------------------------------------------
    // Krok A: Budowa wierzchołków Parteru (indeksy 0..num_stops-1)
    // -------------------------------------------------------------------------
    graph.stop_ids.reserve(dataset.stops.size());
    graph.stop_id_to_idx.reserve(dataset.stops.size());
    graph.nodes.reserve(dataset.stops.size() * 4);

    for (const auto& s : dataset.stops) {
        if (graph.stop_id_to_idx.contains(s.stop_id)) {
            continue;
        }
        const uint32_t stop_idx = static_cast<uint32_t>(graph.stop_ids.size());
        graph.stop_ids.push_back(s.stop_id);
        graph.stop_id_to_idx.emplace(s.stop_id, stop_idx);
        graph.nodes.push_back(Node{
            .lat = s.stop_lat,
            .lon = s.stop_lon,
            .stop_idx = stop_idx,
            .pattern_idx = INVALID_IDX,
            .mode_bit = 0,
            .alight_sec = 0
        });
    }
    graph.num_stops = static_cast<uint32_t>(graph.stop_ids.size());
    stats.parter_nodes = graph.num_stops;

    // Słownik linii (gtfs_routes)
    std::unordered_map<std::string, TempRouteMeta> route_map;
    route_map.reserve(dataset.routes.size());
    graph.route_ids.reserve(dataset.routes.size());
    graph.route_short_names.reserve(dataset.routes.size());

    for (const auto& r : dataset.routes) {
        if (route_map.contains(r.route_id)) {
            continue;
        }
        const uint32_t route_idx = static_cast<uint32_t>(graph.route_ids.size());
        graph.route_ids.push_back(r.route_id);
        graph.route_short_names.push_back(r.route_short_name);
        route_map.emplace(r.route_id, TempRouteMeta{route_idx, map_route_type(r.route_type)});
    }

    // Słownik kursów i kalendarzy (gtfs_trips)
    std::unordered_map<std::string, uint32_t> service_map;
    auto get_or_add_service = [&](const std::string& service_id) -> uint32_t {
        auto [it, inserted] = service_map.emplace(service_id, static_cast<uint32_t>(graph.service_ids.size()));
        if (inserted) {
            graph.service_ids.push_back(service_id);
        }
        return it->second;
    };

    std::unordered_map<std::string, TempTripMeta> trip_meta_map;
    trip_meta_map.reserve(dataset.trips.size());

    for (const auto& t : dataset.trips) {
        auto r_it = route_map.find(t.route_id);
        if (r_it == route_map.end()) {
            continue;
        }
        const uint32_t service_idx = get_or_add_service(t.service_id);
        trip_meta_map.emplace(t.trip_id, TempTripMeta{
            .route_idx = r_it->second.route_idx,
            .service_idx = service_idx,
            .direction_id = t.direction_id,
            .mode_bit = r_it->second.mode_bit
        });
    }

    // -------------------------------------------------------------------------
    // Krok B: Wyznaczanie pattern_id, tworzenie Pięter i kubełków rozkładu
    // -------------------------------------------------------------------------
    std::vector<std::vector<Edge>> adj(graph.num_stops);
    std::vector<uint8_t> has_outgoing_board(graph.num_stops, 0);

    std::unordered_map<PatternKey, uint32_t, PatternKeyHash> pattern_map;
    std::vector<std::vector<uint32_t>> pattern_transit_edges;
    std::unordered_map<SegmentKey, uint32_t, SegmentKeyHash> segment_map;
    std::vector<std::vector<TempConn>> edge_buckets;
    std::vector<uint32_t> trip_segment_counts;

    std::unordered_set<std::string> finished_trips;
    finished_trips.reserve(dataset.trips.size());

    auto flush_trip = [&](const std::string& trip_id, std::vector<ParsedTripStop>& trip_stops) {
        if (trip_stops.size() < 2) {
            ++stats.trips_skipped;
            return;
        }
        if (!std::is_sorted(trip_stops.begin(), trip_stops.end(),
                            [](const ParsedTripStop& a, const ParsedTripStop& b) {
                                return a.stop_sequence < b.stop_sequence;
                            })) {
            std::sort(trip_stops.begin(), trip_stops.end(),
                      [](const ParsedTripStop& a, const ParsedTripStop& b) {
                          return a.stop_sequence < b.stop_sequence;
                      });
        }
        auto t_it = trip_meta_map.find(trip_id);
        if (t_it == trip_meta_map.end()) {
            ++stats.trips_skipped;
            return;
        }
        const TempTripMeta& tmeta = t_it->second;

        PatternKey pkey;
        pkey.route_idx = tmeta.route_idx;
        pkey.direction_id = tmeta.direction_id;
        pkey.stops.reserve(trip_stops.size());
        for (const auto& st : trip_stops) {
            pkey.stops.push_back(PatternStop{
                .stop_idx = st.stop_idx,
                .can_board = st.can_board,
                .can_alight = st.can_alight
            });
        }

        uint32_t pattern_idx = 0;
        auto p_it = pattern_map.find(pkey);
        if (p_it != pattern_map.end()) {
            pattern_idx = p_it->second;
        } else {
            pattern_idx = graph.num_patterns++;
            pattern_map.emplace(pkey, pattern_idx);

            const size_t len = pkey.stops.size();
            std::vector<uint32_t> floor_nodes(len);
            const uint16_t alight_sec = alight_cost_for_mode(tmeta.mode_bit);

            for (size_t k = 0; k < len; ++k) {
                const auto& ps = pkey.stops[k];
                const uint32_t floor_node = static_cast<uint32_t>(graph.nodes.size());
                floor_nodes[k] = floor_node;

                graph.nodes.push_back(Node{
                    .lat = graph.nodes[ps.stop_idx].lat,
                    .lon = graph.nodes[ps.stop_idx].lon,
                    .stop_idx = ps.stop_idx,
                    .pattern_idx = pattern_idx,
                    .mode_bit = tmeta.mode_bit,
                    .alight_sec = alight_sec
                });
                adj.emplace_back();
                ++stats.floor_nodes;

                if (ps.can_board && k + 1 < len) {
                    adj[ps.stop_idx].push_back(Edge{
                        .target_node = floor_node,
                        .type = EdgeType::BOARD,
                        .payload = 0
                    });
                    has_outgoing_board[ps.stop_idx] = 1;
                    ++stats.board_edges;
                }
                if (ps.can_alight && k > 0) {
                    adj[floor_node].push_back(Edge{
                        .target_node = ps.stop_idx,
                        .type = EdgeType::ALIGHT,
                        .payload = 0
                    });
                    ++stats.alight_edges;
                }
            }

            std::vector<uint32_t> te_ids;
            te_ids.reserve(len - 1);
            for (size_t k = 0; k + 1 < len; ++k) {
                const SegmentKey skey{
                    .from_stop_idx = pkey.stops[k].stop_idx,
                    .to_stop_idx = pkey.stops[k + 1].stop_idx,
                    .mode_bit = tmeta.mode_bit
                };
                uint32_t seg_id = 0;
                auto [s_it, s_inserted] = segment_map.emplace(
                    skey, static_cast<uint32_t>(graph.segments.size())
                );
                if (s_inserted) {
                    graph.segments.push_back(PhysicalSegment{
                        .from_stop_idx = skey.from_stop_idx,
                        .to_stop_idx = skey.to_stop_idx,
                        .mode_bit = skey.mode_bit
                    });
                }
                seg_id = s_it->second;

                const uint32_t te_idx = static_cast<uint32_t>(graph.transit_edges.size());
                graph.transit_edges.push_back(TransitEdgeMeta{
                    .first_conn = 0,
                    .last_conn = 0,
                    .physical_segment_id = seg_id
                });
                edge_buckets.emplace_back();

                adj[floor_nodes[k]].push_back(Edge{
                    .target_node = floor_nodes[k + 1],
                    .type = EdgeType::BOARDED_TRANSIT,
                    .payload = te_idx
                });
                te_ids.push_back(te_idx);
            }
            pattern_transit_edges.push_back(std::move(te_ids));
        }

        const uint32_t trip_internal_id = static_cast<uint32_t>(graph.trip_ids.size());
        graph.trip_ids.push_back(trip_id);
        graph.trip_id_to_internal.emplace(trip_id, trip_internal_id);

        const auto& te_ids = pattern_transit_edges[pattern_idx];
        const size_t seg_count = trip_stops.size() - 1;
        trip_segment_counts.push_back(static_cast<uint32_t>(seg_count));

        for (size_t k = 0; k < seg_count; ++k) {
            const uint32_t dep = trip_stops[k].departure_sec;
            const uint32_t arr = std::max(dep, trip_stops[k + 1].arrival_sec);
            edge_buckets[te_ids[k]].push_back(TempConn{
                .trip_internal_id = trip_internal_id,
                .seq_k = static_cast<uint32_t>(k),
                .service_idx = tmeta.service_idx,
                .scheduled_dep = dep,
                .scheduled_arr = arr,
                .can_board = static_cast<uint8_t>(trip_stops[k].can_board ? 1 : 0),
                .can_alight = static_cast<uint8_t>(trip_stops[k + 1].can_alight ? 1 : 0)
            });
        }
        ++stats.trips_loaded;
    };

    std::string current_trip_id;
    std::vector<ParsedTripStop> current_stops;
    current_stops.reserve(64);

    for (const auto& row : dataset.stop_times) {
        if (row.trip_id != current_trip_id) {
            if (!current_trip_id.empty()) {
                finished_trips.insert(current_trip_id);
                flush_trip(current_trip_id, current_stops);
                current_stops.clear();
            }
            if (finished_trips.contains(row.trip_id)) {
                throw std::invalid_argument(
                    "GtfsDataset::stop_times must be grouped contiguously by trip_id: " + row.trip_id
                );
            }
            current_trip_id = row.trip_id;
        }

        auto s_it = graph.stop_id_to_idx.find(row.stop_id);
        if (s_it == graph.stop_id_to_idx.end()) {
            ++stats.stop_times_skipped;
            continue;
        }

        current_stops.push_back(ParsedTripStop{
            .stop_sequence = row.stop_sequence,
            .stop_idx = s_it->second,
            .arrival_sec = parse_gtfs_time(row.arrival_time),
            .departure_sec = parse_gtfs_time(row.departure_time),
            .can_board = (row.pickup_type != 1),
            .can_alight = (row.drop_off_type != 1)
        });
    }
    if (!current_trip_id.empty()) {
        flush_trip(current_trip_id, current_stops);
    }

    // -------------------------------------------------------------------------
    // Krok C: Sortowanie połączeń, spięcie next_conn_idx i budowa trip_index CSR
    // -------------------------------------------------------------------------
    const size_t num_trips = graph.trip_ids.size();
    std::vector<std::vector<uint32_t>> trip_to_conn_pos(num_trips);
    size_t total_conns = 0;
    for (size_t t = 0; t < num_trips; ++t) {
        trip_to_conn_pos[t].assign(trip_segment_counts[t], INVALID_IDX);
        total_conns += trip_segment_counts[t];
    }
    graph.connections.reserve(total_conns);

    // Przebieg 1: sortowanie kubełków i zapis do płaskiego `connections`
    for (uint32_t te = 0; te < graph.transit_edges.size(); ++te) {
        auto& bucket = edge_buckets[te];
        std::sort(bucket.begin(), bucket.end(), [](const TempConn& a, const TempConn& b) {
            if (a.scheduled_dep != b.scheduled_dep) {
                return a.scheduled_dep < b.scheduled_dep;
            }
            if (a.scheduled_arr != b.scheduled_arr) {
                return a.scheduled_arr < b.scheduled_arr;
            }
            return a.trip_internal_id < b.trip_internal_id;
        });

        graph.transit_edges[te].first_conn = static_cast<uint32_t>(graph.connections.size());
        for (const auto& tc : bucket) {
            const uint32_t conn_idx = static_cast<uint32_t>(graph.connections.size());
            graph.connections.push_back(TripConnection{
                .trip_internal_id = tc.trip_internal_id,
                .service_idx = tc.service_idx,
                .scheduled_dep = tc.scheduled_dep,
                .scheduled_arr = tc.scheduled_arr,
                .next_conn_idx = INVALID_IDX,
                .can_board = tc.can_board,
                .can_alight = tc.can_alight
            });
            trip_to_conn_pos[tc.trip_internal_id][tc.seq_k] = conn_idx;
        }
        graph.transit_edges[te].last_conn = static_cast<uint32_t>(graph.connections.size());

        // Natychmiastowe zwolnienie pamięci przetworzonego kubełka
        std::vector<TempConn>().swap(bucket);
    }

    // Przebieg 2: spięcie next_conn_idx oraz budowa płaskiego CSR kursów
    graph.trip_first_ref.reserve(num_trips + 1);
    graph.trip_conn_indices.reserve(total_conns);
    for (size_t t = 0; t < num_trips; ++t) {
        graph.trip_first_ref.push_back(static_cast<uint32_t>(graph.trip_conn_indices.size()));
        const auto& pos = trip_to_conn_pos[t];
        for (size_t k = 0; k < pos.size(); ++k) {
            const uint32_t c_idx = pos[k];
            graph.trip_conn_indices.push_back(c_idx);
            if (k + 1 < pos.size()) {
                graph.connections[c_idx].next_conn_idx = pos[k + 1];
            }
        }
    }
    graph.trip_first_ref.push_back(static_cast<uint32_t>(graph.trip_conn_indices.size()));

    // Zbudowanie kalendarza aktywnych service_idx per data
    {
        std::unordered_map<std::string, std::unordered_set<uint32_t>> date_sets;
        for (const auto& cal : dataset.calendar_dates) {
            auto s_it = service_map.find(cal.service_id);
            if (s_it == service_map.end()) {
                continue;
            }
            if (cal.exception_type == 1) {
                date_sets[cal.date].insert(s_it->second);
            } else if (cal.exception_type == 2) {
                auto d_it = date_sets.find(cal.date);
                if (d_it != date_sets.end()) {
                    d_it->second.erase(s_it->second);
                }
            }
        }
        for (auto& [date, svc_set] : date_sets) {
            std::vector<uint32_t> sorted_svcs(svc_set.begin(), svc_set.end());
            std::sort(sorted_svcs.begin(), sorted_svcs.end());
            graph.calendar.active_services_by_date.emplace(date, std::move(sorted_svcs));
        }
    }

    // -------------------------------------------------------------------------
    // Krok D: Krawędzie piesze WALK (siatka przestrzenna <= max_walk_distance_meters)
    // -------------------------------------------------------------------------
    if (options.max_walk_distance_meters > 0.0 && options.walk_speed_mps > 0.0 && graph.num_stops > 1) {
        constexpr double kMetersPerDegLat = 111320.0;
        // Kosinus dla średniej szerokości Warszawy (~52.23°N)
        constexpr double kMetersPerDegLonWarsaw = 68180.0;

        const double cell_lat = options.max_walk_distance_meters / kMetersPerDegLat;
        const double cell_lon = options.max_walk_distance_meters / kMetersPerDegLonWarsaw;

        auto to_cell = [&](double lat, double lon) -> GridCell {
            return GridCell{
                .gx = static_cast<int32_t>(std::floor(lat / cell_lat)),
                .gy = static_cast<int32_t>(std::floor(lon / cell_lon))
            };
        };

        std::unordered_map<GridCell, std::vector<uint32_t>, GridCellHash> grid;
        grid.reserve(graph.num_stops);
        for (uint32_t u = 0; u < graph.num_stops; ++u) {
            grid[to_cell(graph.nodes[u].lat, graph.nodes[u].lon)].push_back(u);
        }

        for (uint32_t u = 0; u < graph.num_stops; ++u) {
            const GridCell center = to_cell(graph.nodes[u].lat, graph.nodes[u].lon);
            for (int32_t dx = -1; dx <= 1; ++dx) {
                for (int32_t dy = -1; dy <= 1; ++dy) {
                    auto cell_it = grid.find(GridCell{center.gx + dx, center.gy + dy});
                    if (cell_it == grid.end()) {
                        continue;
                    }
                    for (uint32_t v : cell_it->second) {
                        if (u == v) {
                            continue;
                        }
                        if (options.skip_walk_to_non_boarding_stops && !has_outgoing_board[v]) {
                            continue;
                        }
                        const double dist = haversine_meters(
                            graph.nodes[u].lat, graph.nodes[u].lon,
                            graph.nodes[v].lat, graph.nodes[v].lon
                        );
                        if (dist <= options.max_walk_distance_meters) {
                            const uint32_t walk_sec = std::max<uint32_t>(
                                1,
                                static_cast<uint32_t>(std::ceil(dist / options.walk_speed_mps))
                            );
                            adj[u].push_back(Edge{
                                .target_node = v,
                                .type = EdgeType::WALK,
                                .payload = walk_sec
                            });
                            ++stats.walk_edges;
                        }
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Krok E: Spłaszczenie list sąsiedztwa `adj` do docelowego CSR
    // -------------------------------------------------------------------------
    const uint32_t total_nodes = static_cast<uint32_t>(graph.nodes.size());
    graph.first_edge.resize(total_nodes + 1);
    size_t total_edges = 0;
    for (const auto& list : adj) {
        total_edges += list.size();
    }
    graph.edges.reserve(total_edges);

    for (uint32_t u = 0; u < total_nodes; ++u) {
        graph.first_edge[u] = static_cast<uint32_t>(graph.edges.size());
        graph.edges.insert(graph.edges.end(), adj[u].begin(), adj[u].end());
    }
    graph.first_edge[total_nodes] = static_cast<uint32_t>(graph.edges.size());

    stats.transit_edges = static_cast<uint32_t>(graph.transit_edges.size());
    stats.connections = static_cast<uint32_t>(graph.connections.size());
    stats.physical_segments = static_cast<uint32_t>(graph.segments.size());
    if (stats_out) {
        *stats_out = stats;
    }

    return graph;
}

} // namespace opti::routing
