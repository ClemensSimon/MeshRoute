/**
 * WalkFlood Routing — Prototype for ESP32 LoRa Mesh
 *
 * Replaces System 5 geo-clustered routing with a simpler approach:
 * 1. FLOOD: If no route known, broadcast to all neighbors
 * 2. WALK:  If route known, send directed to best next-hop
 * 3. MINI-FLOOD: If walk fails, send to 2 best-scoring neighbors
 *
 * Route learning is passive — routes are learned from overheard traffic.
 * MPR (Multi-Point Relay) reduces broadcast overhead.
 *
 * Memory: 256 entries * 12 bytes = 3 KB route table
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// ── Configuration ──────────────────────────────────────────────

#ifndef WF_MAX_TABLE_SIZE
#define WF_MAX_TABLE_SIZE    256
#endif

#ifndef WF_MAX_NEIGHBORS
#define WF_MAX_NEIGHBORS      32
#endif

#ifndef WF_MAX_MPR
#define WF_MAX_MPR            16
#endif

#ifndef WF_MAX_PATH_LEN
#define WF_MAX_PATH_LEN        8
#endif

#define WF_ROUTE_EXPIRE_MS  300000   // 5 minutes
#define WF_NEIGHBOR_EXPIRE_MS 300000 // 5 minutes

// ── Route Entry (12 bytes, packed) ─────────────────────────────

typedef struct __attribute__((packed)) {
    uint32_t dest_id;        // destination node
    uint32_t next_hop;       // who to send to (0 = unknown)
    uint8_t  hop_count;      // hops to destination
    uint8_t  quality;        // link quality 0-255 (mapped from float 0-1)
    uint16_t last_seen;      // seconds since boot, wraps at 65535
} wf_route_entry_t;

// ── Neighbor Entry ─────────────────────────────────────────────

typedef struct {
    uint32_t node_id;
    float    quality;        // link quality 0.0 - 1.0
    uint8_t  degree;         // how many neighbors this neighbor has (from OGM)
    uint32_t last_seen_ms;   // millis() when last heard
} wf_neighbor_t;

// ── WalkFlood State ────────────────────────────────────────────

typedef struct {
    uint32_t my_id;

    // Route table
    wf_route_entry_t routes[WF_MAX_TABLE_SIZE];
    uint16_t route_count;

    // Neighbor table
    wf_neighbor_t neighbors[WF_MAX_NEIGHBORS];
    uint8_t neighbor_count;

    // MPR set (nodes we relay broadcasts for)
    uint32_t mpr_set[WF_MAX_MPR];
    uint8_t  mpr_count;

    // Stats
    uint32_t walks;          // directed sends
    uint32_t floods;         // full floods
    uint32_t mini_floods;    // mini-floods (2 best neighbors)
} wf_state_t;

// ── Walk Score Result ──────────────────────────────────────────

typedef struct {
    uint32_t neighbor_id;
    float    score;
} wf_walk_score_t;

// ── Core API ───────────────────────────────────────────────────

/**
 * Initialize WalkFlood state with empty tables.
 */
void wf_init(wf_state_t *state);

/**
 * Learn a direct neighbor from OGM or received packet.
 * @param node_id   Neighbor's node ID
 * @param quality   Link quality 0.0 - 1.0 (from RSSI)
 * @param degree    Neighbor's reported neighbor count (from OGM, 0 if unknown)
 */
void wf_learn_neighbor(wf_state_t *state, uint32_t node_id, float quality, uint8_t degree);

/**
 * Learn routes from an overheard packet's source path.
 * Each node in the path teaches us a route to the originator.
 *
 * Example: path = [A, B, C, D] means A sent via B via C to us (D).
 * We learn: A reachable via B (3 hops), B reachable via B (2 hops),
 *           C reachable via C (1 hop, direct neighbor).
 *
 * @param path      Array of node IDs from source to last relay
 * @param path_len  Number of entries in path
 * @param quality   Quality of the link we received this on
 */
void wf_learn_from_packet(wf_state_t *state, const uint32_t *path,
                           uint8_t path_len, float quality);

/**
 * Look up the best next hop for a destination.
 * @return next_hop node ID, or 0 if no route known
 */
uint32_t wf_get_next_hop(const wf_state_t *state, uint32_t dest_id);

/**
 * Score a neighbor for the "walk" phase toward a destination.
 * Higher score = better choice.
 *
 * Formula: has_route * 1000 + quality * 10 + degree * 0.1 - hop_count
 *
 * @return score (negative means neighbor has no info about dest)
 */
float wf_walk_score(const wf_state_t *state, uint32_t neighbor_id, uint32_t dest_id);

/**
 * Get the N best neighbors for walking toward a destination.
 * Fills out_scores[] sorted by score (highest first).
 * @param max_results  Max entries to fill
 * @return Number of entries filled (may be 0)
 */
uint8_t wf_get_best_walkers(const wf_state_t *state, uint32_t dest_id,
                             wf_walk_score_t *out_scores, uint8_t max_results);

/**
 * Remove routes older than WF_ROUTE_EXPIRE_MS.
 * Also removes stale neighbors.
 * @param now_ms  Current millis()
 */
void wf_expire_routes(wf_state_t *state, uint32_t now_ms);

// ── MPR (Multi-Point Relay) ────────────────────────────────────

/**
 * Compute MPR set from current neighbor table.
 * Selects minimal set of neighbors that cover all 2-hop neighbors.
 * Call after neighbor table changes.
 */
void wf_compute_mpr_set(wf_state_t *state);

/**
 * Check if we should relay a broadcast from the given node.
 * Returns true if node_id is in our MPR set (i.e., that node selected us as MPR).
 *
 * Note: In a full implementation, the originator would announce its MPR set.
 * For this prototype, we relay if the sender is our neighbor and we are in
 * a "good position" (have neighbors the sender doesn't reach).
 */
bool wf_is_mpr(const wf_state_t *state, uint32_t node_id);

/**
 * Get route entry for a destination (for inspection/debug).
 * @return Pointer to route entry, or NULL if not found
 */
const wf_route_entry_t *wf_get_route(const wf_state_t *state, uint32_t dest_id);

#ifdef __cplusplus
}
#endif
