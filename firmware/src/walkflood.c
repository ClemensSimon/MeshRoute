/**
 * WalkFlood Routing — Implementation
 *
 * Simple route-learning mesh router for LoRa.
 * All lookups are linear scans — fast enough for 256 entries on ESP32.
 */

#include "walkflood.h"
#include <string.h>

// ── Helpers ────────────────────────────────────────────────────

static int _find_route(const wf_state_t *state, uint32_t dest_id) {
    for (uint16_t i = 0; i < state->route_count; i++) {
        if (state->routes[i].dest_id == dest_id) return (int)i;
    }
    return -1;
}

static int _find_neighbor(const wf_state_t *state, uint32_t node_id) {
    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        if (state->neighbors[i].node_id == node_id) return (int)i;
    }
    return -1;
}

/**
 * Convert millis() to 16-bit seconds (wraps at ~18 hours).
 * Good enough for 5-minute expiry.
 */
static uint16_t _ms_to_sec16(uint32_t ms) {
    return (uint16_t)(ms / 1000);
}

// ── Init ───────────────────────────────────────────────────────

void wf_init(wf_state_t *state) {
    memset(state, 0, sizeof(wf_state_t));
}

// ── Neighbor Learning ──────────────────────────────────────────

void wf_learn_neighbor(wf_state_t *state, uint32_t node_id, float quality, uint8_t degree) {
    if (node_id == 0 || node_id == state->my_id) return;

    int idx = _find_neighbor(state, node_id);

    if (idx >= 0) {
        // Update existing neighbor with EMA
        wf_neighbor_t *n = &state->neighbors[idx];
        n->quality = 0.7f * n->quality + 0.3f * quality;
        n->degree = degree;
        n->last_seen_ms = 0; // Caller sets this via expire_routes timing
        return;
    }

    // New neighbor
    if (state->neighbor_count < WF_MAX_NEIGHBORS) {
        wf_neighbor_t *n = &state->neighbors[state->neighbor_count];
        n->node_id = node_id;
        n->quality = quality;
        n->degree = degree;
        n->last_seen_ms = 0;
        state->neighbor_count++;
    } else {
        // Evict worst quality neighbor
        uint8_t worst = 0;
        float worst_q = state->neighbors[0].quality;
        for (uint8_t i = 1; i < state->neighbor_count; i++) {
            if (state->neighbors[i].quality < worst_q) {
                worst_q = state->neighbors[i].quality;
                worst = i;
            }
        }
        if (quality > worst_q) {
            wf_neighbor_t *n = &state->neighbors[worst];
            n->node_id = node_id;
            n->quality = quality;
            n->degree = degree;
            n->last_seen_ms = 0;
        }
    }

    // Also add a direct route (1 hop)
    int ridx = _find_route(state, node_id);
    if (ridx >= 0) {
        // Update if better or same
        wf_route_entry_t *r = &state->routes[ridx];
        if (r->hop_count >= 1) {
            r->next_hop = node_id;
            r->hop_count = 1;
            r->quality = (uint8_t)(quality * 255.0f);
            r->last_seen = 0; // Will be set by caller
        }
    } else if (state->route_count < WF_MAX_TABLE_SIZE) {
        wf_route_entry_t *r = &state->routes[state->route_count];
        r->dest_id = node_id;
        r->next_hop = node_id;
        r->hop_count = 1;
        r->quality = (uint8_t)(quality * 255.0f);
        r->last_seen = 0;
        state->route_count++;
    }
}

// ── Packet-based Route Learning ────────────────────────────────

void wf_learn_from_packet(wf_state_t *state, const uint32_t *path,
                           uint8_t path_len, float quality) {
    if (path_len == 0) return;

    // path[0] = original source, path[path_len-1] = last relay before us
    // We learn routes to each node in the path.
    //
    // To reach path[0] (source), our next_hop is path[path_len-1]
    //   (or path[0] if path_len==1, meaning direct neighbor)
    // Hop count to path[i] = path_len - i

    for (uint8_t i = 0; i < path_len; i++) {
        uint32_t dest = path[i];
        if (dest == 0 || dest == state->my_id) continue;

        uint8_t hops = path_len - i;
        // Next hop is always the node closest to us in the path
        // If path_len >= 1, next_hop for any dest is path[path_len - 1]
        // (the node that relayed to us)
        uint32_t next = (path_len >= 1) ? path[path_len - 1] : dest;
        // For the last entry in path (direct relay), next_hop is that node itself
        if (i == path_len - 1) {
            next = dest;
            hops = 1;
        }

        // Quality degrades with hops
        float q = quality;
        for (uint8_t h = 1; h < hops; h++) {
            q *= 0.8f; // 20% loss per hop
        }

        int ridx = _find_route(state, dest);
        if (ridx >= 0) {
            wf_route_entry_t *r = &state->routes[ridx];
            // Update if: fewer hops, or same hops but better quality
            uint8_t new_q = (uint8_t)(q * 255.0f);
            if (hops < r->hop_count ||
                (hops == r->hop_count && new_q > r->quality)) {
                r->next_hop = next;
                r->hop_count = hops;
                r->quality = new_q;
                r->last_seen = 0; // refreshed
            } else {
                // Just refresh timestamp even if not better
                r->last_seen = 0;
            }
        } else if (state->route_count < WF_MAX_TABLE_SIZE) {
            wf_route_entry_t *r = &state->routes[state->route_count];
            r->dest_id = dest;
            r->next_hop = next;
            r->hop_count = hops;
            r->quality = (uint8_t)(q * 255.0f);
            r->last_seen = 0;
            state->route_count++;
        }
    }
}

// ── Route Lookup ───────────────────────────────────────────────

uint32_t wf_get_next_hop(const wf_state_t *state, uint32_t dest_id) {
    int idx = _find_route(state, dest_id);
    if (idx < 0) return 0;
    return state->routes[idx].next_hop;
}

const wf_route_entry_t *wf_get_route(const wf_state_t *state, uint32_t dest_id) {
    int idx = _find_route(state, dest_id);
    if (idx < 0) return NULL;
    return &state->routes[idx];
}

// ── Walk Scoring ───────────────────────────────────────────────

float wf_walk_score(const wf_state_t *state, uint32_t neighbor_id, uint32_t dest_id) {
    // Find the neighbor
    int nidx = _find_neighbor(state, neighbor_id);
    if (nidx < 0) return -1.0f;

    const wf_neighbor_t *n = &state->neighbors[nidx];
    float score = 0.0f;

    // Check if this neighbor is the destination itself
    if (neighbor_id == dest_id) {
        return 10000.0f; // best possible
    }

    // Check if we have a route through this neighbor to the destination
    // (i.e., a route entry where next_hop == neighbor_id and dest == dest_id)
    int ridx = _find_route(state, dest_id);
    if (ridx >= 0 && state->routes[ridx].next_hop == neighbor_id) {
        // This neighbor is on the path
        score += 1000.0f;
        score -= (float)state->routes[ridx].hop_count;
    }

    // Neighbor quality bonus
    score += n->quality * 10.0f;

    // Degree bonus (more connected = more likely to know routes)
    score += (float)n->degree * 0.1f;

    return score;
}

uint8_t wf_get_best_walkers(const wf_state_t *state, uint32_t dest_id,
                             wf_walk_score_t *out_scores, uint8_t max_results) {
    if (max_results == 0 || state->neighbor_count == 0) return 0;

    // Score all neighbors
    wf_walk_score_t all[WF_MAX_NEIGHBORS];
    uint8_t count = 0;

    for (uint8_t i = 0; i < state->neighbor_count; i++) {
        float s = wf_walk_score(state, state->neighbors[i].node_id, dest_id);
        if (s >= 0.0f) {
            all[count].neighbor_id = state->neighbors[i].node_id;
            all[count].score = s;
            count++;
        }
    }

    // Simple selection sort for top N
    uint8_t filled = 0;
    for (uint8_t n = 0; n < max_results && n < count; n++) {
        // Find best remaining
        uint8_t best_idx = 0;
        float best_score = -1.0f;
        for (uint8_t i = 0; i < count; i++) {
            // Skip already selected (score set to -2)
            if (all[i].score < -1.5f) continue;
            if (all[i].score > best_score) {
                best_score = all[i].score;
                best_idx = i;
            }
        }
        if (best_score < -1.5f) break;

        out_scores[filled++] = all[best_idx];
        all[best_idx].score = -2.0f; // mark as used
    }

    return filled;
}

// ── Route Expiry ───────────────────────────────────────────────

void wf_expire_routes(wf_state_t *state, uint32_t now_ms) {
    uint16_t now_sec = _ms_to_sec16(now_ms);
    uint16_t expire_sec = (uint16_t)(WF_ROUTE_EXPIRE_MS / 1000);

    // Expire routes
    for (int i = (int)state->route_count - 1; i >= 0; i--) {
        uint16_t age;
        if (now_sec >= state->routes[i].last_seen) {
            age = now_sec - state->routes[i].last_seen;
        } else {
            // Wrapped around
            age = (uint16_t)(65535U - state->routes[i].last_seen + now_sec + 1U);
        }

        if (age > expire_sec) {
            // Remove by shifting
            for (uint16_t j = (uint16_t)i; j < state->route_count - 1; j++) {
                state->routes[j] = state->routes[j + 1];
            }
            state->route_count--;
        }
    }

    // Expire neighbors
    uint32_t neighbor_expire = WF_NEIGHBOR_EXPIRE_MS;
    for (int i = (int)state->neighbor_count - 1; i >= 0; i--) {
        if (state->neighbors[i].last_seen_ms > 0 &&
            (now_ms - state->neighbors[i].last_seen_ms) > neighbor_expire) {
            // Remove by shifting
            for (uint8_t j = (uint8_t)i; j < state->neighbor_count - 1; j++) {
                state->neighbors[j] = state->neighbors[j + 1];
            }
            state->neighbor_count--;
        }
    }
}

// ── MPR Computation ────────────────────────────────────────────

void wf_compute_mpr_set(wf_state_t *state) {
    // Greedy MPR selection:
    // Goal: select minimal set of 1-hop neighbors that covers all 2-hop neighbors.
    //
    // Since we don't have full 2-hop topology in this prototype,
    // we use a heuristic: select neighbors with highest degree
    // (most connected neighbors are best relays).

    state->mpr_count = 0;

    if (state->neighbor_count == 0) return;

    // Simple greedy: pick neighbors sorted by degree (highest first)
    // until we have enough or run out
    bool selected[WF_MAX_NEIGHBORS];
    memset(selected, 0, sizeof(selected));

    // We want at most WF_MAX_MPR relays, but at least enough to cover
    // In the simple case: select all neighbors with degree > 0,
    // up to WF_MAX_MPR.
    for (uint8_t round = 0; round < WF_MAX_MPR && round < state->neighbor_count; round++) {
        // Find unselected neighbor with highest degree
        int best = -1;
        uint8_t best_degree = 0;
        for (uint8_t i = 0; i < state->neighbor_count; i++) {
            if (selected[i]) continue;
            if (best < 0 || state->neighbors[i].degree > best_degree) {
                best = (int)i;
                best_degree = state->neighbors[i].degree;
            }
        }
        if (best < 0) break;

        // Only select neighbors that actually have other connections
        // (degree > 1, meaning they reach nodes we might not)
        if (best_degree <= 1 && round > 0) break;

        selected[best] = true;
        state->mpr_set[state->mpr_count++] = state->neighbors[best].node_id;
    }
}

bool wf_is_mpr(const wf_state_t *state, uint32_t node_id) {
    for (uint8_t i = 0; i < state->mpr_count; i++) {
        if (state->mpr_set[i] == node_id) return true;
    }
    return false;
}
