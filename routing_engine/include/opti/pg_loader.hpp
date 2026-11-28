#pragma once

#include "opti/gtfs_data.hpp"

#include <string>

namespace opti::routing {

struct PgConnectionConfig {
    std::string host = "localhost";
    int port = 5432;
    std::string dbname = "opti_transit_pipeline";
    std::string user = "postgres";
    std::string password = "postgres";

    // Wczytuje konfigurację ze zmiennych środowiskowych (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)
    static PgConnectionConfig from_env();

    std::string to_conninfo() const;
};

class PostgresGtfsLoader {
public:
    explicit PostgresGtfsLoader(PgConnectionConfig config);

    // Pobiera wszystkie 5 tabel GTFS w jednej transakcji REPEATABLE READ READ ONLY
    GtfsDataset load_dataset() const;

private:
    PgConnectionConfig config_;
};

} // namespace opti::routing
