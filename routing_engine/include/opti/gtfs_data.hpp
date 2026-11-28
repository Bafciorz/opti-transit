#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace opti::routing {

struct StopRow {
    std::string stop_id;
    double stop_lat = 0.0;
    double stop_lon = 0.0;
};

struct RouteRow {
    std::string route_id;
    std::string route_short_name;
    int32_t route_type = 3;
};

struct TripRow {
    std::string trip_id;
    std::string route_id;
    std::string service_id;
    int32_t direction_id = -1;
};

struct StopTimeRow {
    std::string trip_id;
    int32_t stop_sequence = 0;
    std::string stop_id;
    std::string arrival_time;
    std::string departure_time;
    int32_t pickup_type = 0;
    int32_t drop_off_type = 0;
};

struct CalendarDateRow {
    std::string service_id;
    std::string date; // YYYY-MM-DD
    int32_t exception_type = 1;
};

struct GtfsDataset {
    std::vector<StopRow> stops;
    std::vector<RouteRow> routes;
    std::vector<TripRow> trips;
    // Wymagane posortowanie po (trip_id, stop_sequence) — tak jak zwraca SQL
    std::vector<StopTimeRow> stop_times;
    std::vector<CalendarDateRow> calendar_dates;
};

} // namespace opti::routing
