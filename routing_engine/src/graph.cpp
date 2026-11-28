#include "opti/graph.hpp"

#include <sstream>

namespace opti::routing {

bool TransitGraph::validate(std::string* error_out) const {
    auto fail = [&](const std::string& msg) {
        if (error_out) {
            *error_out = msg;
        }
        return false;
    };

    if (first_edge.size() != nodes.size() + 1) {
        return fail("first_edge.size() must equal nodes.size() + 1");
    }
    if (!first_edge.empty() && first_edge.back() != edges.size()) {
        return fail("first_edge.back() must equal edges.size()");
    }
    if (num_stops > nodes.size()) {
        return fail("num_stops exceeds nodes.size()");
    }

    // Sprawdź spójność Parteru vs Pięter
    for (uint32_t i = 0; i < nodes.size(); ++i) {
        if (i < num_stops) {
            if (nodes[i].pattern_idx != INVALID_IDX) {
                return fail("Parter node has non-INVALID pattern_idx");
            }
            if (nodes[i].stop_idx != i) {
                return fail("Parter node stop_idx mismatch");
            }
        } else {
            if (nodes[i].pattern_idx == INVALID_IDX) {
                return fail("Floor node has INVALID pattern_idx");
            }
            if (nodes[i].stop_idx >= num_stops) {
                return fail("Floor node stop_idx out of range");
            }
        }

        if (first_edge[i] > first_edge[i + 1]) {
            return fail("first_edge is not monotonically non-decreasing");
        }

        uint32_t transit_out_count = 0;
        for (uint32_t e = first_edge[i]; e < first_edge[i + 1]; ++e) {
            const Edge& edge = edges[e];
            if (edge.target_node >= nodes.size()) {
                return fail("Edge target_node out of range");
            }
            switch (edge.type) {
                case EdgeType::BOARD:
                    if (i >= num_stops || edge.target_node < num_stops) {
                        return fail("BOARD edge must go from Parter to Floor");
                    }
                    break;
                case EdgeType::ALIGHT:
                    if (i < num_stops || edge.target_node >= num_stops) {
                        return fail("ALIGHT edge must go from Floor to Parter");
                    }
                    break;
                case EdgeType::WALK:
                    if (i >= num_stops || edge.target_node >= num_stops) {
                        return fail("WALK edge must connect Parter to Parter");
                    }
                    if (edge.payload == 0) {
                        return fail("WALK edge has 0 duration");
                    }
                    break;
                case EdgeType::BOARDED_TRANSIT:
                    if (i < num_stops || edge.target_node < num_stops) {
                        return fail("BOARDED_TRANSIT edge must connect Floor to Floor");
                    }
                    if (edge.payload >= transit_edges.size()) {
                        return fail("BOARDED_TRANSIT payload out of transit_edges bounds");
                    }
                    ++transit_out_count;
                    break;
            }
        }

        if (transit_out_count > 1) {
            return fail("Floor node has more than one outgoing BOARDED_TRANSIT edge");
        }
    }

    // Sprawdź krawędzie rozkładowe i posortowanie TripConnection
    for (size_t te = 0; te < transit_edges.size(); ++te) {
        const auto& meta = transit_edges[te];
        if (meta.first_conn >= meta.last_conn || meta.last_conn > connections.size()) {
            std::ostringstream oss;
            oss << "Invalid TransitEdgeMeta range at index " << te;
            return fail(oss.str());
        }
        if (meta.physical_segment_id >= segments.size()) {
            return fail("TransitEdgeMeta physical_segment_id out of range");
        }
        for (uint32_t c = meta.first_conn; c < meta.last_conn; ++c) {
            const auto& conn = connections[c];
            if (conn.scheduled_arr < conn.scheduled_dep) {
                return fail("TripConnection has scheduled_arr < scheduled_dep");
            }
            if (conn.trip_internal_id >= trip_ids.size()) {
                return fail("TripConnection trip_internal_id out of range");
            }
            if (c + 1 < meta.last_conn && connections[c + 1].scheduled_dep < conn.scheduled_dep) {
                return fail("TripConnection array is not sorted by scheduled_dep");
            }
            if (conn.next_conn_idx != INVALID_IDX) {
                if (conn.next_conn_idx >= connections.size()) {
                    return fail("TripConnection next_conn_idx out of range");
                }
                const auto& next_conn = connections[conn.next_conn_idx];
                if (next_conn.trip_internal_id != conn.trip_internal_id) {
                    return fail("next_conn_idx points to a different trip_internal_id");
                }
                if (next_conn.scheduled_dep < conn.scheduled_arr) {
                    return fail("next_conn_idx departs before previous segment arrival");
                }
            }
        }
    }

    // Sprawdź płaski CSR indeksów kursów
    if (trip_first_ref.size() != trip_ids.size() + 1) {
        return fail("trip_first_ref.size() must equal trip_ids.size() + 1");
    }
    if (!trip_first_ref.empty() && trip_first_ref.back() != trip_conn_indices.size()) {
        return fail("trip_first_ref.back() must equal trip_conn_indices.size()");
    }
    for (uint32_t t = 0; t < trip_ids.size(); ++t) {
        if (trip_first_ref[t] >= trip_first_ref[t + 1]) {
            return fail("Every loaded trip must have at least one connection");
        }
        for (uint32_t r = trip_first_ref[t]; r < trip_first_ref[t + 1]; ++r) {
            uint32_t c_idx = trip_conn_indices[r];
            if (c_idx >= connections.size()) {
                return fail("trip_conn_indices entry out of range");
            }
            if (connections[c_idx].trip_internal_id != t) {
                return fail("trip_conn_indices entry belongs to wrong trip");
            }
        }
    }

    return true;
}

} // namespace opti::routing
