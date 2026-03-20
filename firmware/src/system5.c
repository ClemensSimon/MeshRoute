/**
 * MeshRoute System 5 — Core Implementation
 *
 * Geo-clustered multi-path routing for ESP32 / Meshtastic.
 * Pure C for maximum portability and minimal RAM usage.
 */

#include "system5.h"
#include <string.h>
#include <math.h>

// ── Geohash ────────────────────────────────────────────────────

static const char GEOHASH_BASE32[] = "0123456789bcdefghjkmnpqrstuvwxyz";

void s5_geohash_encode(float lat, float lon, uint8_t precision, s5_geohash_t *out) {
    float lat_range[2] = {-90.0f, 90.0f};
    float lon_range[2] = {-180.0f, 180.0f};
    uint8_t bits = 0;
    uint8_t char_idx = 0;
    uint8_t pos = 0;
    bool is_lon = true;

    if (precision > S5_GEOHASH_PRECISION) precision = S5_GEOHASH_PRECISION;

    while (pos < precision) {
        float mid;
        if (is_lon) {
            mid = (lon_range[0] + lon_range[1]) / 2.0f;
            if (lon >= mid) {
                char_idx = (char_idx << 1) | 1;
                lon_range[0] = mid;
            } else {
                char_idx = char_idx << 1;
                lon_range[1] = mid;
            }
        } else {
            mid = (lat_range[0] + lat_range[1]) / 2.0f;
            if (lat >= mid) {
                char_idx = (char_idx << 1) | 1;
                lat_range[0] = mid;
            } else {
                char_idx = char_idx << 1;
                lat_range[1] = mid;
            }
        }
        is_lon = !is_lon;
        bits++;
        if (bits == 5) {
            out->hash[pos++] = GEOHASH_BASE32[char_idx];
            bits = 0;
            char_idx = 0;
        }
    }
    out->hash[pos] = '\0';
}

uint8_t s5_geohash_common_prefix(const s5_geohash_t *a, const s5_geohash_t *b) {
    uint8_t len = 0;
    for (uint8_t i = 0; i < S5_GEOHASH_PRECISION; i++) {
        if (a->hash[i] == '\0' || b->hash[i] == '\0') break;
        if (a->hash[i] != b->hash[i]) break;
        len++;
    }
    return len;
}

// ── Init ───────────────────────────────────────────────────────

static uint8_t _geohash_to_cluster_id(const s5_geohash_t *gh) {
    uint8_t cid = 0;
    for (uint8_t i = 0; i < S5_GEOHASH_PRECISION && gh->hash[i]; i++) {
        cid = cid * 31 + (uint8_t)gh->hash[i];
    }
    return cid;
}

void s5_init(s5_node_state_t *state) {
    memset(state, 0, sizeof(s5_node_state_t));
    state->my_cluster_id = 0xFF; // unassigned
}

// ── Position & Clustering ──────────────────────────────────────

static void _recompute_cluster(s5_node_state_t *state) {
    // Assign cluster based on geohash prefix match with neighbors
    // Simple: cluster_id = first char of geohash (gives ~32 possible clusters)
    if (state->my_geohash.hash[0] == '\0') {
        state->my_cluster_id = 0xFF;
        return;
    }

    state->my_cluster_id = _geohash_to_cluster_id(&state->my_geohash);

    // Check if we're a border node (have neighbors in different clusters)
    state->my_is_border = false;
    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        if (state->neighbors[i].cluster_id != state->my_cluster_id) {
            state->my_is_border = true;
            break;
        }
    }
}

void s5_update_position(s5_node_state_t *state, float lat, float lon) {
    state->my_lat = lat;
    state->my_lon = lon;

    s5_geohash_t old_hash = state->my_geohash;
    s5_geohash_encode(lat, lon, S5_GEOHASH_PRECISION, &state->my_geohash);

    // Recompute cluster if geohash changed
    if (memcmp(&old_hash, &state->my_geohash, sizeof(s5_geohash_t)) != 0) {
        _recompute_cluster(state);
    }
}

// ── Neighbor Management ────────────────────────────────────────

static int _find_neighbor(const s5_node_state_t *state, s5_node_id_t id) {
    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        if (state->neighbors[i].id == id) return i;
    }
    return -1;
}

void s5_update_neighbor(s5_node_state_t *state, s5_node_id_t id,
                         float lat, float lon, uint8_t battery_pct,
                         int8_t snr, float link_quality) {
    int idx = _find_neighbor(state, id);

    if (idx < 0) {
        // New neighbor
        if (state->neighbor_count >= S5_MAX_NEIGHBORS) {
            // Evict worst neighbor (lowest link quality)
            uint8_t worst = 0;
            float worst_q = state->neighbors[0].link_quality;
            for (uint8_t i = 1; i < state->neighbor_count; i++) {
                if (state->neighbors[i].link_quality < worst_q) {
                    worst_q = state->neighbors[i].link_quality;
                    worst = i;
                }
            }
            // Only evict if new neighbor is better
            if (link_quality <= worst_q) return;
            idx = worst;
        } else {
            idx = state->neighbor_count++;
        }
    }

    s5_neighbor_t *n = &state->neighbors[idx];
    n->id = id;
    n->lat = lat;
    n->lon = lon;
    s5_geohash_encode(lat, lon, S5_GEOHASH_PRECISION, &n->geohash);
    n->battery_pct = battery_pct;
    n->snr = snr;
    // Exponential moving average for link quality
    if (n->link_quality > 0.01f) {
        n->link_quality = 0.7f * n->link_quality + 0.3f * link_quality;
    } else {
        n->link_quality = link_quality;
    }
    n->last_heard_ms = 0; // caller should set this via maintenance

    n->cluster_id = _geohash_to_cluster_id(&n->geohash);

    // Re-check border status
    _recompute_cluster(state);
}

void s5_remove_neighbor(s5_node_state_t *state, s5_node_id_t id) {
    int idx = _find_neighbor(state, id);
    if (idx < 0) return;
    if (state->neighbor_count == 0) return; // guard against underflow

    // Shift remaining neighbors
    for (uint8_t i = idx; i < state->neighbor_count - 1; i++) {
        state->neighbors[i] = state->neighbors[i + 1];
    }
    state->neighbor_count--;
    _recompute_cluster(state);
}

// ── Route Weight Computation ───────────────────────────────────

static float _compute_weight(float quality, float load, float battery) {
    return S5_ALPHA * quality
         + S5_BETA * (1.0f - load)
         + S5_GAMMA * battery;
}

// ── QoS Gate ───────────────────────────────────────────────────

static bool _qos_gate(float nhs, uint8_t priority) {
    uint8_t max_priority;
    if (nhs >= S5_NHS_HEALTHY)       max_priority = 7;
    else if (nhs >= S5_NHS_MODERATE)  max_priority = 5;
    else if (nhs >= S5_NHS_DEGRADED)  max_priority = 3;
    else if (nhs >= S5_NHS_CRITICAL)  max_priority = 1;
    else                              max_priority = 0; // SOS only
    return priority <= max_priority;
}

// ── Routing Decision ───────────────────────────────────────────

// Simple routing table: array of route entries
// In a real implementation this would use a hash map
static s5_route_entry_t _route_table[S5_MAX_NODES];
static uint8_t _route_table_size = 0;

static s5_route_entry_t *_find_route_entry(s5_node_id_t dest_id) {
    for (uint8_t i = 0; i < _route_table_size; i++) {
        if (_route_table[i].dest_id == dest_id) return &_route_table[i];
    }
    return NULL;
}

static s5_route_t *_select_best_route(s5_route_entry_t *entry, const s5_node_state_t *state,
                                       uint8_t exclude_mask) {
    if (!entry || entry->route_count == 0) return NULL;

    s5_route_t *best = NULL;
    float best_weight = -1.0f;

    for (uint8_t i = 0; i < entry->route_count; i++) {
        if (exclude_mask & (1 << i)) continue; // skip already-tried routes
        s5_route_t *r = &entry->routes[i];
        if (r->fail_count >= S5_MAX_RETRIES) continue; // route is dead

        // Gradual backpressure: penalize weight based on load
        float load_penalty = 1.0f;
        if (r->load > S5_BACKPRESSURE_THRESHOLD) {
            // Linear penalty from 0.8 to 0.95, hard block above 0.95
            if (r->load > S5_BACKPRESSURE_HARD_BLOCK) continue;
            load_penalty = 1.0f - ((r->load - S5_BACKPRESSURE_THRESHOLD)
                           / (S5_BACKPRESSURE_HARD_BLOCK - S5_BACKPRESSURE_THRESHOLD));
        }

        // Recompute weight with current data and backpressure
        r->weight = _compute_weight(r->quality, r->load, r->battery) * load_penalty;

        if (r->weight > best_weight) {
            best_weight = r->weight;
            best = r;
        }
    }
    return best;
}

s5_route_decision_t s5_route(s5_node_state_t *state, const s5_packet_t *packet) {
    s5_route_decision_t decision = {
        .action = S5_ROUTE_DROP,
        .next_hop = 0,
        .route_index = 0,
        .used_fallback = false,
    };

    // Is this packet for us?
    if (packet->dst == state->my_id) {
        decision.action = S5_ROUTE_DELIVERED;
        return decision;
    }

    // QoS gate
    float nhs = s5_get_nhs(state);
    if (!_qos_gate(nhs, packet->priority)) {
        decision.action = S5_ROUTE_DROP;
        return decision;
    }

    // TTL check (dynamic max based on estimated network size)
    uint8_t max_hops = s5_dynamic_max_hops(state->neighbor_count * 6); // rough estimate
    if (packet->hop_count >= packet->ttl || packet->hop_count >= max_hops) {
        decision.action = S5_ROUTE_DROP;
        return decision;
    }

    // If packet already has a System5 next_hop and it's for us to forward
    if (packet->is_system5 && packet->next_hop != 0 && packet->next_hop != state->my_id) {
        // Not for us — this shouldn't happen in directed routing
        decision.action = S5_ROUTE_DROP;
        return decision;
    }

    // Look up routing table — try multiple routes before falling back
    s5_route_entry_t *entry = _find_route_entry(packet->dst);
    if (entry) {
        uint8_t exclude_mask = 0;
        uint8_t max_attempts = S5_MAX_ROUTE_ATTEMPTS;
        if (max_attempts > entry->route_count) max_attempts = entry->route_count;

        for (uint8_t attempt = 0; attempt < max_attempts; attempt++) {
            s5_route_t *route = _select_best_route(entry, state, exclude_mask);
            if (!route || route->path_len < 2) break;

            uint8_t route_idx = (uint8_t)(route - entry->routes);
            exclude_mask |= (1 << route_idx);

            // Find ourselves in the path and get the next hop
            for (uint8_t i = 0; i < route->path_len - 1; i++) {
                if (route->path[i] == state->my_id) {
                    decision.action = S5_ROUTE_DIRECT;
                    decision.next_hop = route->path[i + 1];
                    decision.route_index = route_idx;
                    return decision;
                }
            }
        }
    }

    // No route found — fallback to managed flooding
    decision.action = S5_ROUTE_FLOOD;
    decision.used_fallback = true;
    return decision;
}

// ── Route Feedback ─────────────────────────────────────────────

void s5_route_feedback(s5_node_state_t *state, s5_node_id_t dest_id,
                        uint8_t route_index, bool success) {
    s5_route_entry_t *entry = _find_route_entry(dest_id);
    if (!entry || route_index >= entry->route_count) return;

    s5_route_t *route = &entry->routes[route_index];
    if (success) {
        route->fail_count = 0;
        route->last_used_ms = 0; // caller sets actual time
        // Boost quality slightly
        route->quality = fminf(1.0f, route->quality * 1.05f);
    } else {
        route->fail_count++;
        // Decay quality
        route->quality *= 0.5f;
    }
    route->weight = _compute_weight(route->quality, route->load, route->battery);
}

// ── Maintenance ────────────────────────────────────────────────

void s5_maintenance(s5_node_state_t *state, uint32_t now_ms) {
    // Prune neighbors not heard from in 5 minutes
    const uint32_t TIMEOUT_MS = 300000;

    for (int i = state->neighbor_count - 1; i >= 0; i--) {
        if (state->neighbors[i].last_heard_ms > 0 &&
            (now_ms - state->neighbors[i].last_heard_ms) > TIMEOUT_MS) {
            s5_remove_neighbor(state, state->neighbors[i].id);
        }
    }

    // Decay route qualities (pheromone evaporation)
    for (uint8_t i = 0; i < _route_table_size; i++) {
        for (uint8_t j = 0; j < _route_table[i].route_count; j++) {
            s5_route_t *r = &_route_table[i].routes[j];
            r->quality *= 0.95f; // 5% decay per maintenance cycle
            r->weight = _compute_weight(r->quality, r->load, r->battery);

            // Remove very low quality routes
            if (r->quality < 0.01f || r->fail_count >= S5_MAX_RETRIES * 2) {
                // Shift remaining routes
                for (uint8_t k = j; k < _route_table[i].route_count - 1; k++) {
                    _route_table[i].routes[k] = _route_table[i].routes[k + 1];
                }
                _route_table[i].route_count--;
                j--;
            }
        }
    }
}

// ── NHS ────────────────────────────────────────────────────────

float s5_get_nhs(const s5_node_state_t *state) {
    if (state->neighbor_count == 0) return 0.0f;

    float avg_quality = 0;
    float avg_battery = 0;
    uint8_t connected = 0;

    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        const s5_neighbor_t *n = &state->neighbors[i];
        if (n->cluster_id == state->my_cluster_id) {
            avg_quality += n->link_quality;
            avg_battery += n->battery_pct / 100.0f;
            if (n->link_quality > 0.1f) connected++;
        }
    }

    uint8_t cluster_neighbors = 0;
    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        if (state->neighbors[i].cluster_id == state->my_cluster_id)
            cluster_neighbors++;
    }

    if (cluster_neighbors == 0) return 0.5f; // no cluster info yet

    avg_quality /= cluster_neighbors;
    avg_battery /= cluster_neighbors;
    float connectivity = (float)connected / cluster_neighbors;

    float nhs = 0.4f * avg_quality + 0.3f * avg_battery + 0.3f * connectivity;
    if (nhs > 1.0f) nhs = 1.0f;
    if (nhs < 0.0f) nhs = 0.0f;
    return nhs;
}

const s5_cluster_t *s5_get_my_cluster(const s5_node_state_t *state) {
    // In a full implementation this would return cluster details
    // For now, return NULL — cluster info computed on-the-fly via NHS
    (void)state;
    return NULL;
}

// ── Adaptive Retries ──────────────────────────────────────────

uint8_t s5_get_retry_count(float link_quality) {
    return (link_quality > 0.5f) ? S5_MAX_RETRIES : S5_MAX_RETRIES_POOR;
}

// ── Dynamic Max Hops ──────────────────────────────────────────

uint8_t s5_dynamic_max_hops(uint8_t estimated_nodes) {
    if (estimated_nodes == 0) return S5_MIN_HOPS;

    // sqrt(n) * 3, clamped to [S5_MIN_HOPS, S5_MAX_HOPS_CAP]
    float hops = sqrtf((float)estimated_nodes) * 3.0f;
    uint8_t result = (uint8_t)hops;
    if (result < S5_MIN_HOPS) result = S5_MIN_HOPS;
    if (result > S5_MAX_HOPS_CAP) result = S5_MAX_HOPS_CAP;
    return result;
}

// ── Cluster Corridor Flooding ─────────────────────────────────

uint8_t s5_get_flood_corridor(const s5_node_state_t *state,
                               uint8_t src_cluster, uint8_t dst_cluster,
                               uint8_t *out_corridor, uint8_t max_len) {
    if (max_len == 0) return 0;
    if (src_cluster == dst_cluster) {
        out_corridor[0] = src_cluster;
        return 1;
    }

    // Build cluster adjacency from neighbor table
    // Each neighbor knows its cluster_id; if different from ours, clusters are adjacent
    // We can only build adjacency from our local view — in practice the Meshtastic
    // NodeDB would provide a fuller picture.

    // Simplified BFS on known cluster adjacency (max S5_MAX_CLUSTERS clusters)
    typedef struct { uint8_t cid; uint8_t parent; uint8_t depth; } bfs_entry_t;
    bfs_entry_t queue[S5_MAX_CLUSTERS];
    uint8_t visited[S5_MAX_CLUSTERS];
    uint8_t visited_count = 0;
    uint8_t q_head = 0, q_tail = 0;

    // Seed: source cluster
    queue[q_tail++] = (bfs_entry_t){ .cid = src_cluster, .parent = 0xFF, .depth = 0 };
    visited[visited_count++] = src_cluster;

    // Build adjacency: cluster pairs visible from our neighbor table
    // adj[i] = set of clusters adjacent to cluster i
    // We store as flat pairs for simplicity
    typedef struct { uint8_t a; uint8_t b; } cpair_t;
    cpair_t adj[S5_MAX_NEIGHBORS];
    uint8_t adj_count = 0;

    // Our cluster connects to neighbor clusters
    for (uint8_t i = 0; i < state->neighbor_count && adj_count < S5_MAX_NEIGHBORS; i++) {
        uint8_t ncid = state->neighbors[i].cluster_id;
        if (ncid != state->my_cluster_id) {
            // Check for duplicate
            bool dup = false;
            for (uint8_t j = 0; j < adj_count; j++) {
                if ((adj[j].a == state->my_cluster_id && adj[j].b == ncid) ||
                    (adj[j].a == ncid && adj[j].b == state->my_cluster_id)) {
                    dup = true; break;
                }
            }
            if (!dup) {
                adj[adj_count++] = (cpair_t){ .a = state->my_cluster_id, .b = ncid };
            }
        }
    }

    // BFS
    while (q_head < q_tail) {
        bfs_entry_t cur = queue[q_head++];
        if (cur.cid == dst_cluster) {
            // Reconstruct path
            uint8_t path[S5_MAX_CLUSTERS];
            uint8_t path_len = 0;
            uint8_t trace = q_head - 1;
            while (trace != 0xFF && path_len < S5_MAX_CLUSTERS) {
                path[path_len++] = queue[trace].cid;
                // Find parent
                if (queue[trace].parent == 0xFF) break;
                bool found = false;
                for (uint8_t k = 0; k < q_head; k++) {
                    if (queue[k].cid == queue[trace].parent) {
                        trace = k;
                        found = true;
                        break;
                    }
                }
                if (!found) break;
            }
            // Reverse into output
            uint8_t out_len = 0;
            for (int k = path_len - 1; k >= 0 && out_len < max_len; k--) {
                out_corridor[out_len++] = path[k];
            }
            return out_len;
        }

        // Expand neighbors
        for (uint8_t i = 0; i < adj_count && q_tail < S5_MAX_CLUSTERS; i++) {
            uint8_t next_cid = 0xFF;
            if (adj[i].a == cur.cid) next_cid = adj[i].b;
            else if (adj[i].b == cur.cid) next_cid = adj[i].a;
            else continue;

            bool vis = false;
            for (uint8_t v = 0; v < visited_count; v++) {
                if (visited[v] == next_cid) { vis = true; break; }
            }
            if (vis) continue;

            visited[visited_count++] = next_cid;
            queue[q_tail++] = (bfs_entry_t){ .cid = next_cid, .parent = cur.cid, .depth = cur.depth + 1 };
        }
    }

    // No path found — return src + dst as fallback
    out_corridor[0] = src_cluster;
    if (max_len > 1) {
        out_corridor[1] = dst_cluster;
        return 2;
    }
    return 1;
}
