#include "opti/graph_builder.hpp"

#include <algorithm>
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace opti::routing;

namespace {

void test_helpers() {
    assert(GraphBuilder::parse_gtfs_time("00:00:00") == 0);
    assert(GraphBuilder::parse_gtfs_time("08:15:30") == 8 * 3600 + 15 * 60 + 30);
    assert(GraphBuilder::parse_gtfs_time("25:10:00") == 25 * 3600 + 10 * 60);

    assert(GraphBuilder::map_route_type(0) == MODE_TRAM);
    assert(GraphBuilder::map_route_type(1) == MODE_METRO);
    assert(GraphBuilder::map_route_type(2) == MODE_RAIL);
    assert(GraphBuilder::map_route_type(3) == MODE_BUS);

    assert(GraphBuilder::alight_cost_for_mode(MODE_METRO) == 60);
    assert(GraphBuilder::alight_cost_for_mode(MODE_BUS) == 15);

    const double dist = GraphBuilder::haversine_meters(52.2300, 21.0100, 52.2300, 21.0100);
    assert(dist == 0.0);
}

void test_patterns_csr_sorting_and_next_conn() {
    GtfsDataset ds;
    ds.stops = {
        {"S_A", 52.2300, 21.0100},
        {"S_B", 52.2310, 21.0120}, // ~175 m od S_A
        {"S_C", 52.2400, 21.0300}, // > 1 km od S_A i S_B
    };
    ds.routes = {
        {"175", "175", 3}, // BUS
        {"9", "9", 0},     // TRAM
    };
    ds.trips = {
        {"T_LATE", "175", "WK", 0},
        {"T_EARLY", "175", "WK", 0},
        {"T_NIGHT", "175", "WK", 1}, // przeciwny kierunek -> osobny pattern
        {"T_TRAM", "9", "WK", 0},    // inny mode_bit -> osobny segment fizyczny na A->B
    };

    // Celowo mieszamy kolejnosc odjazdow miedzy kursami (T_LATE przed T_EARLY),
    // ale wewnatrz kazdego kursu kolejnosc przystankow jest rosnaca
    ds.stop_times = {
        // T_EARLY: A -> B -> C
        {"T_EARLY", 1, "S_A", "08:00:00", "08:00:00", 0, 0},
        {"T_EARLY", 2, "S_B", "08:05:00", "08:06:00", 0, 0},
        {"T_EARLY", 3, "S_C", "08:12:00", "08:12:00", 0, 0},
        // T_LATE: ten sam pattern co T_EARLY (A -> B -> C), odjazd pozniejszy
        {"T_LATE", 1, "S_A", "09:00:00", "09:00:00", 0, 0},
        {"T_LATE", 2, "S_B", "09:05:00", "09:06:00", 0, 0},
        {"T_LATE", 3, "S_C", "09:12:00", "09:12:00", 0, 0},
        // T_NIGHT: kierunek przeciwny C -> B -> A, czas po polnocy (>= 24:00:00)
        {"T_NIGHT", 1, "S_C", "24:30:00", "24:30:00", 0, 0},
        {"T_NIGHT", 2, "S_B", "24:36:00", "24:37:00", 0, 0},
        {"T_NIGHT", 3, "S_A", "24:42:00", "24:42:00", 0, 0},
        // T_TRAM: A -> B (ta sama para przystankow co 175, ale TRAM)
        {"T_TRAM", 1, "S_A", "08:10:00", "08:10:00", 0, 0},
        {"T_TRAM", 2, "S_B", "08:14:00", "08:14:00", 0, 0},
    };
    ds.calendar_dates = {
        {"WK", "2026-11-28", 1},
    };

    GraphBuildStats stats;
    TransitGraph g = GraphBuilder::build(ds, GraphBuildOptions{}, &stats);

    std::string err;
    assert(g.validate(&err) && err.empty());

    // 3 przystanki Parteru
    assert(g.num_stops == 3);
    assert(stats.parter_nodes == 3);
    // Wzorce: (175 dir 0: 3 pietra) + (175 dir 1: 3 pietra) + (9 dir 0: 2 pietra) = 3 wzorce, 8 pieter
    assert(g.num_patterns == 3);
    assert(stats.floor_nodes == 8);
    assert(g.nodes.size() == 11);
    assert(stats.trips_loaded == 4);

    // Odcinki fizyczne: (A->B, BUS), (B->C, BUS), (C->B, BUS), (B->A, BUS), (A->B, TRAM) = 5
    assert(stats.physical_segments == 5);

    // Krawedzie WALK: S_A <-> S_B maja ~175m <= 350m, wiec 2 krawedzie WALK (S_A->S_B i S_B->S_A)
    assert(stats.walk_edges == 2);

    // Sprawdz posortowanie i next_conn_idx dla T_EARLY i T_LATE na pierwszej krawedzi BOARDED_TRANSIT linii 175
    const uint32_t early_id = g.trip_id_to_internal.at("T_EARLY");
    const uint32_t late_id = g.trip_id_to_internal.at("T_LATE");
    assert(g.trip_first_ref[early_id + 1] - g.trip_first_ref[early_id] == 2);

    const uint32_t early_c0 = g.trip_conn_indices[g.trip_first_ref[early_id]];
    const uint32_t early_c1 = g.trip_conn_indices[g.trip_first_ref[early_id] + 1];
    assert(g.connections[early_c0].next_conn_idx == early_c1);
    assert(g.connections[early_c1].next_conn_idx == INVALID_IDX);

    const uint32_t late_c0 = g.trip_conn_indices[g.trip_first_ref[late_id]];
    assert(g.connections[early_c0].scheduled_dep < g.connections[late_c0].scheduled_dep);
    // W tym samym TransitEdgeMeta `early_c0` musi poprzedzac `late_c0` w tablicy connections
    assert(early_c0 < late_c0);

    // Sprawdz kalendarz
    const auto& active = g.calendar.active_services_by_date.at("2026-11-28");
    assert(active.size() == 1);
}

void test_loop_route_and_calendar_exceptions() {
    // Linia okrezna odwiedzajaca S_A dwukrotnie (S_A -> S_B -> S_A)
    GtfsDataset ds;
    ds.stops = {
        {"S_A", 52.2300, 21.0100},
        {"S_B", 52.2500, 21.0500},
    };
    ds.routes = {
        {"L1", "L1", 3},
    };
    ds.trips = {
        {"LOOP_1", "L1", "SVC_A", 0},
        {"LOOP_2", "L1", "SVC_B", 0},
    };
    ds.stop_times = {
        {"LOOP_1", 1, "S_A", "10:00:00", "10:00:00", 0, 1}, // tylko wsiadanie na starcie
        {"LOOP_1", 2, "S_B", "10:10:00", "10:11:00", 0, 0},
        {"LOOP_1", 3, "S_A", "10:20:00", "10:20:00", 1, 0}, // tylko wysiadanie na koncu
        {"LOOP_2", 1, "S_A", "11:00:00", "11:00:00", 0, 1},
        {"LOOP_2", 2, "S_B", "11:10:00", "11:11:00", 0, 0},
        {"LOOP_2", 3, "S_A", "11:20:00", "11:20:00", 1, 0},
    };
    ds.calendar_dates = {
        {"SVC_A", "2026-11-28", 1},
        {"SVC_B", "2026-11-28", 1},
        {"SVC_B", "2026-11-28", 2}, // wykluczone
    };

    GraphBuildStats stats;
    TransitGraph g = GraphBuilder::build(ds, GraphBuildOptions{}, &stats);

    std::string err;
    assert(g.validate(&err) && err.empty());

    // Mimo ze S_A powtarza sie 2x w tym samym kursie, oba kursy dziela 1 pattern (3 pietra: pos 0, 1, 2)
    assert(g.num_patterns == 1);
    assert(stats.floor_nodes == 3);
    // Z pierwszego przystanku tylko BOARD (brak ALIGHT), ze srodkowego BOARD+ALIGHT, z koncowego tylko ALIGHT
    assert(stats.board_edges == 2);
    assert(stats.alight_edges == 2);

    // SVC_B zostalo usuniete przez exception_type = 2
    const auto& active = g.calendar.active_services_by_date.at("2026-11-28");
    assert(active.size() == 1);
}

void test_shared_physical_segment_across_bus_lines() {
    GtfsDataset ds;
    ds.stops = {
        {"S1", 52.2300, 21.0100},
        {"S2", 52.2350, 21.0200},
    };
    ds.routes = {
        {"111", "111", 3},
        {"116", "116", 3},
    };
    ds.trips = {
        {"T111", "111", "WK", 0},
        {"T116", "116", "WK", 0},
    };
    ds.stop_times = {
        {"T111", 1, "S1", "12:00:00", "12:00:00", 0, 0},
        {"T111", 2, "S2", "12:03:00", "12:03:00", 0, 0},
        {"T116", 1, "S1", "12:05:00", "12:05:00", 0, 0},
        {"T116", 2, "S2", "12:08:00", "12:08:00", 0, 0},
    };

    GraphBuildStats stats;
    TransitGraph g = GraphBuilder::build(ds, GraphBuildOptions{}, &stats);
    assert(g.validate());

    // Dwie rozne linie (2 patterny, 2 transit_edges), ale jeden wspolny odcinek fizyczny (S1->S2, BUS)
    assert(stats.transit_edges == 2);
    assert(stats.physical_segments == 1);
    assert(g.transit_edges[0].physical_segment_id == g.transit_edges[1].physical_segment_id);
}

} // namespace

int main() {
    test_helpers();
    test_patterns_csr_sorting_and_next_conn();
    test_loop_route_and_calendar_exceptions();
    test_shared_physical_segment_across_bus_lines();

    std::cout << "All routing graph builder tests passed!\n";
    return 0;
}
