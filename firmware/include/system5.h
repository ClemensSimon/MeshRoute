/**
 * MeshRoute System 5 — Geo-Clustered Multi-Path Routing for Meshtastic
 *
 * Core header: data structures for nodes, links, clusters, routes.
 * Designed to integrate with Meshtastic firmware as a routing module.
 *
 * Memory budget: ~8KB RAM for 100-node network on ESP32.
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// ── Configuration ──────────────────────────────────────────────

// Allow compile-time override via -D flags (e.g., for reduced memory builds)
#ifndef S5_MAX_NODES
#define S5_MAX_NODES         100   // max tracked nodes in network
#endif
#ifndef S5_MAX_NEIGHBORS
#define S5_MAX_NEIGHBORS      16   // max neighbors per node
#endif
#ifndef S5_MAX_ROUTES
#define S5_MAX_ROUTES          5   // max cached routes per destination
#endif
#ifndef S5_MAX_HOPS
#define S5_MAX_HOPS           20   // default hop cap (dynamic via s5_dynamic_max_hops)
#endif
#ifndef S5_MIN_HOPS
#define S5_MIN_HOPS           15   // floor for dynamic hop limit
#endif
#ifndef S5_MAX_HOPS_CAP
#define S5_MAX_HOPS_CAP       40   // ceiling for dynamic hop limit
#endif
#ifndef S5_MAX_PATH_LEN
#define S5_MAX_PATH_LEN       15   // max hops in a single route
#endif
#ifndef S5_MAX_CLUSTERS
#define S5_MAX_CLUSTERS        8   // max geo-clusters
#endif
#define S5_GEOHASH_PRECISION   4   // characters for cluster grouping
#define S5_BRIDGE_LINKS_PER_PAIR 2 // bridge links between cluster pairs

// Route weight parameters: W(r) = alpha*Q + beta*(1-Load) + gamma*Batt
#define S5_ALPHA  0.4f
#define S5_BETA   0.35f
#define S5_GAMMA  0.25f

// QoS: Network Health Score thresholds -> max allowed priority
#define S5_NHS_HEALTHY    0.8f  // priorities 0-7 allowed
#define S5_NHS_MODERATE   0.6f  // priorities 0-5 allowed
#define S5_NHS_DEGRADED   0.4f  // priorities 0-3 allowed
#define S5_NHS_CRITICAL   0.2f  // priorities 0-1 allowed

#define S5_BACKPRESSURE_THRESHOLD 0.8f
#define S5_BACKPRESSURE_HARD_BLOCK 0.95f // fully block route only above this
#define S5_MAX_RETRIES     3    // retries per hop for good links (quality > 0.5)
#define S5_MAX_RETRIES_POOR 5   // retries per hop for poor links (quality <= 0.5)
#define S5_MAX_ROUTE_ATTEMPTS 5 // try up to N different routes before fallback

// Proactive path probing
#define S5_PROBE_INTERVAL_MS  60000  // probe one secondary route every 60s
#define S5_PROBE_STALE_MS    120000  // probe routes not used in 2 minutes
#define S5_PROBE_TIMEOUT_MS   10000  // probe reply timeout

// ── Geohash ────────────────────────────────────────────────────

typedef struct {
    char hash[S5_GEOHASH_PRECISION + 1]; // null-terminated
} s5_geohash_t;

/**
 * Encode GPS coordinates to geohash.
 * @param lat  Latitude in degrees (-90 to 90)
 * @param lon  Longitude in degrees (-180 to 180)
 * @param precision  Number of characters (1-8)
 * @param out  Output geohash struct
 */
void s5_geohash_encode(float lat, float lon, uint8_t precision, s5_geohash_t *out);

/**
 * Common prefix length of two geohashes.
 * @return Number of matching characters from the start
 */
uint8_t s5_geohash_common_prefix(const s5_geohash_t *a, const s5_geohash_t *b);

// ── Node ───────────────────────────────────────────────────────

typedef uint32_t s5_node_id_t;

typedef struct {
    s5_node_id_t id;
    float lat, lon;              // GPS position
    s5_geohash_t geohash;
    uint8_t cluster_id;          // assigned cluster (0-255, 0xFF = unassigned)
    bool is_border;              // has neighbors in other clusters
    uint8_t battery_pct;         // 0-100
    uint8_t queue_len;           // current TX queue length
    float link_quality;          // average link quality to this node (0-1)
    int8_t snr;                  // last received SNR in dB
    uint32_t last_heard_ms;      // millis() of last reception from this node
} s5_neighbor_t;

typedef struct {
    s5_node_id_t my_id;
    float my_lat, my_lon;
    s5_geohash_t my_geohash;
    uint8_t my_cluster_id;
    bool my_is_border;
    uint8_t my_battery_pct;

    // Silencing state
    bool silent;                 // if true: listen only, no TX (except direct replies)
    uint32_t silence_until_ms;   // millis() when silence expires (0 = permanent until unsilenced)
    float redundancy_score;      // 0=critical, 1=fully redundant

    // Per-destination sequence numbers for gap detection.
    // Indexed by neighbor table slot (0..S5_MAX_NEIGHBORS-1), NOT by raw node ID.
    // Use s5_get_seq() to look up/increment. Destinations beyond neighbor table
    // use a small LRU cache (seq_lru_*). Total: 16*2 + 16*6 = 128 bytes.
    uint16_t seq_neighbor[S5_MAX_NEIGHBORS]; // seq for known neighbors
    struct { s5_node_id_t id; uint16_t seq; } seq_lru[S5_MAX_NEIGHBORS]; // LRU for others
    uint8_t seq_lru_count;

    // Neighbor table
    s5_neighbor_t neighbors[S5_MAX_NEIGHBORS];
    uint8_t neighbor_count;
} s5_node_state_t;

// ── Route ──────────────────────────────────────────────────────

typedef struct {
    s5_node_id_t path[S5_MAX_PATH_LEN];
    uint8_t path_len;            // number of nodes in path (including src+dst)
    float quality;               // product of link qualities along path
    float load;                  // average load of intermediate nodes
    float battery;               // min battery of intermediate nodes
    float weight;                // computed W(r)
    uint32_t last_used_ms;       // millis() of last successful use
    uint8_t fail_count;          // consecutive failures on this route
    uint32_t last_probed_ms;     // millis() of last probe sent on this route
    bool probe_pending;          // true if awaiting probe reply
} s5_route_t;

typedef struct {
    s5_node_id_t dest_id;
    s5_route_t routes[S5_MAX_ROUTES];
    uint8_t route_count;
} s5_route_entry_t;

// ── Cluster ────────────────────────────────────────────────────

typedef struct {
    uint8_t id;
    char geohash_prefix[S5_GEOHASH_PRECISION + 1];
    uint8_t member_count;
    uint8_t border_count;
    float nhs;                   // Network Health Score (0-1)
} s5_cluster_t;

// ── Packet ─────────────────────────────────────────────────────

typedef struct {
    s5_node_id_t src;
    s5_node_id_t dst;
    uint32_t packet_id;          // unique ID for dedup
    uint8_t priority;            // QoS 0-7 (0 = highest / SOS)
    uint8_t hop_count;           // current hop count
    uint8_t ttl;                 // remaining hops
    s5_node_id_t next_hop;       // system5 routing: next node on path (0 = flood)
    bool is_system5;             // true = directed routing, false = legacy flood
    uint16_t payload_len;
    uint8_t *payload;            // pointer to payload (not owned)
} s5_packet_t;

// ── Router API ─────────────────────────────────────────────────

/**
 * Routing decision result.
 */
typedef enum {
    S5_ROUTE_DIRECT,       // send to specific next_hop
    S5_ROUTE_FLOOD,        // managed flooding fallback
    S5_ROUTE_DROP,         // QoS gate blocked / no route
    S5_ROUTE_DELIVERED,    // packet is for us
} s5_route_action_t;

typedef struct {
    s5_route_action_t action;
    s5_node_id_t next_hop;       // valid when action == S5_ROUTE_DIRECT
    uint8_t route_index;         // which route was selected
    bool used_fallback;
} s5_route_decision_t;

/**
 * Initialize System 5 router state.
 * Call once at boot.
 */
void s5_init(s5_node_state_t *state);

/**
 * Update own position. Triggers cluster recomputation if geohash changed.
 */
void s5_update_position(s5_node_state_t *state, float lat, float lon);

/**
 * Register a neighbor (from received OGM or packet).
 * Updates link quality, SNR, position.
 */
void s5_update_neighbor(s5_node_state_t *state, s5_node_id_t id,
                         float lat, float lon, uint8_t battery_pct,
                         int8_t snr, float link_quality);

/**
 * Remove a neighbor (timed out / dead).
 */
void s5_remove_neighbor(s5_node_state_t *state, s5_node_id_t id);

/**
 * Compute or refresh routing table for a destination.
 * Uses BFS over known topology.
 */
void s5_compute_routes(s5_node_state_t *state, s5_node_id_t dest_id);

/**
 * Main routing decision: what to do with a packet.
 * Called for both incoming and locally-generated packets.
 *
 * @param state   Our node state
 * @param packet  The packet to route
 * @return        Routing decision (direct, flood, drop, delivered)
 */
s5_route_decision_t s5_route(s5_node_state_t *state, const s5_packet_t *packet);

/**
 * Report that a route attempt succeeded or failed.
 * Used for route quality learning / failover.
 */
void s5_route_feedback(s5_node_state_t *state, s5_node_id_t dest_id,
                        uint8_t route_index, bool success);

/**
 * Periodic maintenance: prune old neighbors, recompute NHS, decay routes.
 * Call every ~30 seconds.
 */
void s5_maintenance(s5_node_state_t *state, uint32_t now_ms);

/**
 * Get current cluster info for this node.
 */
const s5_cluster_t *s5_get_my_cluster(const s5_node_state_t *state);

/**
 * Get Network Health Score for local cluster.
 */
float s5_get_nhs(const s5_node_state_t *state);

/**
 * Get adaptive retry count based on link quality.
 * Returns S5_MAX_RETRIES for good links (>0.5), S5_MAX_RETRIES_POOR for poor.
 */
uint8_t s5_get_retry_count(float link_quality);

/**
 * Compute dynamic max hops based on estimated network size.
 * Returns max(S5_MIN_HOPS, min(S5_MAX_HOPS_CAP, sqrt(n_nodes) * 3)).
 */
uint8_t s5_dynamic_max_hops(uint8_t estimated_nodes);

/**
 * Get cluster corridor for scoped flooding fallback.
 * Returns cluster IDs along the shortest cluster-level path from src to dst.
 * @param src_cluster  Source cluster ID
 * @param dst_cluster  Destination cluster ID
 * @param out_corridor Output array of cluster IDs
 * @param max_len      Max length of output array
 * @return Number of cluster IDs in corridor (0 if no path found)
 */
uint8_t s5_get_flood_corridor(const s5_node_state_t *state,
                               uint8_t src_cluster, uint8_t dst_cluster,
                               uint8_t *out_corridor, uint8_t max_len);

/**
 * Proactive path probing: pick one stale secondary route and return
 * probe info. Called during maintenance (~every 60s).
 *
 * @param state      Node state
 * @param now_ms     Current millis()
 * @param out_dest   Output: destination node of route to probe
 * @param out_next   Output: next hop to send probe to
 * @param out_ridx   Output: route index being probed
 * @return true if a probe should be sent
 */
bool s5_pick_probe_target(s5_node_state_t *state, uint32_t now_ms,
                           s5_node_id_t *out_dest, s5_node_id_t *out_next,
                           uint8_t *out_ridx);

/**
 * Handle a received probe reply. Updates route quality with real measurement.
 *
 * @param state      Node state
 * @param dest_id    Destination that was probed
 * @param route_idx  Route index that was probed
 * @param rtt_ms     Round-trip time of the probe
 * @param success    true if probe reached destination and returned
 */
void s5_handle_probe_reply(s5_node_state_t *state, s5_node_id_t dest_id,
                            uint8_t route_idx, uint32_t rtt_ms, bool success);

#ifdef __cplusplus
}
#endif
