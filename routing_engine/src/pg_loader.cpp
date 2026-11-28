#include "opti/pg_loader.hpp"

#include <libpq-fe.h>

#include <cstdlib>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace opti::routing {

namespace {

struct PgConnDeleter {
    void operator()(PGconn* conn) const noexcept {
        if (conn) {
            PQfinish(conn);
        }
    }
};

struct PgResDeleter {
    void operator()(PGresult* res) const noexcept {
        if (res) {
            PQclear(res);
        }
    }
};

using PgConnPtr = std::unique_ptr<PGconn, PgConnDeleter>;
using PgResPtr = std::unique_ptr<PGresult, PgResDeleter>;

std::string env_or(const char* name, const char* fallback) {
    const char* val = std::getenv(name);
    if (val && *val != '\0') {
        return std::string(val);
    }
    return std::string(fallback);
}

PgResPtr exec_checked(PGconn* conn, const char* sql, ExecStatusType expected) {
    PgResPtr res(PQexec(conn, sql));
    if (!res || PQresultStatus(res.get()) != expected) {
        std::ostringstream oss;
        oss << "PostgreSQL query failed: " << sql << " | Error: " << PQerrorMessage(conn);
        throw std::runtime_error(oss.str());
    }
    return res;
}

} // namespace

PgConnectionConfig PgConnectionConfig::from_env() {
    PgConnectionConfig cfg;
    cfg.host = env_or("DB_HOST", "localhost");
    cfg.port = std::stoi(env_or("DB_PORT", "5432"));
    cfg.dbname = env_or("DB_NAME", "opti_transit_pipeline");
    cfg.user = env_or("DB_USER", "postgres");
    cfg.password = env_or("DB_PASSWORD", "postgres");
    return cfg;
}

std::string PgConnectionConfig::to_conninfo() const {
    std::ostringstream oss;
    oss << "host=" << host
        << " port=" << port
        << " dbname=" << dbname
        << " user=" << user
        << " password=" << password;
    return oss.str();
}

PostgresGtfsLoader::PostgresGtfsLoader(PgConnectionConfig config)
    : config_(std::move(config)) {}

GtfsDataset PostgresGtfsLoader::load_dataset() const {
    PgConnPtr conn(PQconnectdb(config_.to_conninfo().c_str()));
    if (!conn || PQstatus(conn.get()) != CONNECTION_OK) {
        std::ostringstream oss;
        oss << "Failed to connect to PostgreSQL: "
            << (conn ? PQerrorMessage(conn.get()) : "allocation failed");
        throw std::runtime_error(oss.str());
    }

    GtfsDataset dataset;

    exec_checked(conn.get(), "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY", PGRES_COMMAND_OK);

    try {
        // 1. Przystanki (Parter)
        {
            PgResPtr res = exec_checked(
                conn.get(),
                "SELECT stop_id, stop_lat, stop_lon "
                "FROM gtfs_stops "
                "ORDER BY stop_id",
                PGRES_TUPLES_OK
            );
            int rows = PQntuples(res.get());
            dataset.stops.reserve(static_cast<size_t>(rows));
            for (int r = 0; r < rows; ++r) {
                StopRow row;
                row.stop_id = PQgetvalue(res.get(), r, 0);
                row.stop_lat = std::strtod(PQgetvalue(res.get(), r, 1), nullptr);
                row.stop_lon = std::strtod(PQgetvalue(res.get(), r, 2), nullptr);
                dataset.stops.push_back(std::move(row));
            }
        }

        // 2. Linie (mapowanie route_type -> mode_bit)
        {
            PgResPtr res = exec_checked(
                conn.get(),
                "SELECT route_id, COALESCE(route_short_name, ''), route_type "
                "FROM gtfs_routes",
                PGRES_TUPLES_OK
            );
            int rows = PQntuples(res.get());
            dataset.routes.reserve(static_cast<size_t>(rows));
            for (int r = 0; r < rows; ++r) {
                RouteRow row;
                row.route_id = PQgetvalue(res.get(), r, 0);
                row.route_short_name = PQgetvalue(res.get(), r, 1);
                row.route_type = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), r, 2), nullptr, 10));
                dataset.routes.push_back(std::move(row));
            }
        }

        // 3. Kursy
        {
            PgResPtr res = exec_checked(
                conn.get(),
                "SELECT trip_id, route_id, service_id, COALESCE(direction_id, -1) AS direction_id "
                "FROM gtfs_trips",
                PGRES_TUPLES_OK
            );
            int rows = PQntuples(res.get());
            dataset.trips.reserve(static_cast<size_t>(rows));
            for (int r = 0; r < rows; ++r) {
                TripRow row;
                row.trip_id = PQgetvalue(res.get(), r, 0);
                row.route_id = PQgetvalue(res.get(), r, 1);
                row.service_id = PQgetvalue(res.get(), r, 2);
                row.direction_id = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), r, 3), nullptr, 10));
                dataset.trips.push_back(std::move(row));
            }
        }

        // 4. Rozkład jazdy (strumieniowo w trybie single-row, bez buforowania całych 8M wierszy przez libpq)
        {
            const char* stop_times_sql =
                "SELECT trip_id, stop_sequence, stop_id, arrival_time, departure_time, "
                "       COALESCE(pickup_type, 0) AS pickup_type, "
                "       COALESCE(drop_off_type, 0) AS drop_off_type "
                "FROM gtfs_stop_times "
                "ORDER BY trip_id, stop_sequence";

            if (PQsendQuery(conn.get(), stop_times_sql) != 1) {
                throw std::runtime_error(std::string("PQsendQuery failed: ") + PQerrorMessage(conn.get()));
            }
            if (PQsetSingleRowMode(conn.get()) != 1) {
                throw std::runtime_error(std::string("PQsetSingleRowMode failed: ") + PQerrorMessage(conn.get()));
            }

            while (PGresult* raw_res = PQgetResult(conn.get())) {
                PgResPtr res(raw_res);
                ExecStatusType status = PQresultStatus(res.get());
                if (status == PGRES_SINGLE_TUPLE) {
                    StopTimeRow row;
                    row.trip_id = PQgetvalue(res.get(), 0, 0);
                    row.stop_sequence = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), 0, 1), nullptr, 10));
                    row.stop_id = PQgetvalue(res.get(), 0, 2);
                    row.arrival_time = PQgetvalue(res.get(), 0, 3);
                    row.departure_time = PQgetvalue(res.get(), 0, 4);
                    row.pickup_type = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), 0, 5), nullptr, 10));
                    row.drop_off_type = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), 0, 6), nullptr, 10));
                    dataset.stop_times.push_back(std::move(row));
                } else if (status == PGRES_TUPLES_OK) {
                    // Koniec strumienia wierszy; pętla pobierze jeszcze finalne nullptr
                    continue;
                } else {
                    std::ostringstream oss;
                    oss << "Streaming gtfs_stop_times failed: " << PQerrorMessage(conn.get());
                    while (PGresult* drain = PQgetResult(conn.get())) {
                        PQclear(drain);
                    }
                    throw std::runtime_error(oss.str());
                }
            }
        }

        // 5. Kalendarz kursowania
        {
            PgResPtr res = exec_checked(
                conn.get(),
                "SELECT service_id, date::text, exception_type "
                "FROM gtfs_calendar_dates "
                "ORDER BY date, service_id",
                PGRES_TUPLES_OK
            );
            int rows = PQntuples(res.get());
            dataset.calendar_dates.reserve(static_cast<size_t>(rows));
            for (int r = 0; r < rows; ++r) {
                CalendarDateRow row;
                row.service_id = PQgetvalue(res.get(), r, 0);
                row.date = PQgetvalue(res.get(), r, 1);
                row.exception_type = static_cast<int32_t>(std::strtol(PQgetvalue(res.get(), r, 2), nullptr, 10));
                dataset.calendar_dates.push_back(std::move(row));
            }
        }

        exec_checked(conn.get(), "COMMIT", PGRES_COMMAND_OK);
    } catch (...) {
        PGresult* rb = PQexec(conn.get(), "ROLLBACK");
        if (rb) {
            PQclear(rb);
        }
        throw;
    }

    return dataset;
}

} // namespace opti::routing
