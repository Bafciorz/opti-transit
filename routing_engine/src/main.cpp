#include "opti/graph_builder.hpp"
#include "opti/pg_loader.hpp"

#include <chrono>
#include <iostream>
#include <string>

int main() {
    using namespace opti::routing;

    try {
        const auto cfg = PgConnectionConfig::from_env();
        std::cout << "[opti-routing] Laczenie z PostgreSQL: "
                  << cfg.host << ":" << cfg.port << "/" << cfg.dbname << "\n";

        const auto t0 = std::chrono::steady_clock::now();
        PostgresGtfsLoader loader(cfg);
        GtfsDataset dataset = loader.load_dataset();
        const auto t1 = std::chrono::steady_clock::now();

        std::cout << "[opti-routing] Wczytano z DB: stops=" << dataset.stops.size()
                  << ", routes=" << dataset.routes.size()
                  << ", trips=" << dataset.trips.size()
                  << ", stop_times=" << dataset.stop_times.size()
                  << ", calendar_dates=" << dataset.calendar_dates.size()
                  << " (" << std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count() << " ms)\n";

        GraphBuildStats stats;
        TransitGraph graph = GraphBuilder::build(dataset, GraphBuildOptions{}, &stats);
        const auto t2 = std::chrono::steady_clock::now();

        std::string validation_error;
        if (!graph.validate(&validation_error)) {
            std::cerr << "[opti-routing] BLAD walidacji grafu: " << validation_error << "\n";
            return 2;
        }

        const double mem_mb = (
            graph.nodes.size() * sizeof(Node) +
            graph.first_edge.size() * sizeof(uint32_t) +
            graph.edges.size() * sizeof(Edge) +
            graph.transit_edges.size() * sizeof(TransitEdgeMeta) +
            graph.connections.size() * sizeof(TripConnection) +
            graph.trip_first_ref.size() * sizeof(uint32_t) +
            graph.trip_conn_indices.size() * sizeof(uint32_t)
        ) / (1024.0 * 1024.0);

        std::cout << "[opti-routing] Graf zbudowany i zwalidowany poprawnie w "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(t2 - t1).count() << " ms\n"
                  << "  - Wierzcholki Parter: " << stats.parter_nodes << "\n"
                  << "  - Wierzcholki Pietra: " << stats.floor_nodes
                  << " (wzorcow tras: " << graph.num_patterns << ")\n"
                  << "  - Krawedzie BOARD:    " << stats.board_edges << "\n"
                  << "  - Krawedzie ALIGHT:   " << stats.alight_edges << "\n"
                  << "  - Krawedzie WALK:     " << stats.walk_edges << "\n"
                  << "  - Krawedzie TRANSIT:  " << stats.transit_edges << "\n"
                  << "  - Polaczenia (conn):  " << stats.connections << "\n"
                  << "  - Kursy zaladowane:   " << stats.trips_loaded
                  << " (pominiete: " << stats.trips_skipped << ")\n"
                  << "  - Fizyczne odcinki:   " << stats.physical_segments << "\n"
                  << "  - Szacowany RAM CSR:  " << mem_mb << " MB\n";

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "[opti-routing] Wyjatek krytyczny: " << ex.what() << "\n";
        return 1;
    }
}
