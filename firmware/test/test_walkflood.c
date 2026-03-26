/**
 * Unit tests for WalkFlood routing.
 * Can run on native platform (no ESP32 needed).
 *
 * Build: pio test -e native
 * Or: gcc -I../include -lm -o test_walkflood test_walkflood.c ../src/walkflood.c && ./test_walkflood
 */

#include "walkflood.h"
#include <stdio.h>
#include <assert.h>
#include <string.h>

#define TEST(name) printf("  TEST: %s ... ", #name)
#define PASS() printf("PASS\n")

// ── Init Tests ─────────────────────────────────────────────────

void test_init(void) {
    TEST(wf_init);
    wf_state_t state;
    wf_init(&state);
    assert(state.route_count == 0);
    assert(state.neighbor_count == 0);
    assert(state.mpr_count == 0);
    assert(state.walks == 0);
    assert(state.floods == 0);
    PASS();
}

// ── Neighbor Learning ──────────────────────────────────────────

void test_learn_neighbor(void) {
    TEST(learn_neighbor);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 3);
    assert(state.neighbor_count == 1);
    assert(state.neighbors[0].node_id == 10);
    assert(state.neighbors[0].quality > 0.8f);
    assert(state.neighbors[0].degree == 3);

    // Also creates a 1-hop route
    assert(state.route_count == 1);
    assert(state.routes[0].dest_id == 10);
    assert(state.routes[0].next_hop == 10);
    assert(state.routes[0].hop_count == 1);

    PASS();
}

void test_learn_neighbor_update(void) {
    TEST(learn_neighbor_update);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.5f, 2);
    wf_learn_neighbor(&state, 10, 0.9f, 5);
    assert(state.neighbor_count == 1);
    // Quality should be EMA: 0.7*0.5 + 0.3*0.9 = 0.62
    assert(state.neighbors[0].quality > 0.6f);
    assert(state.neighbors[0].degree == 5);

    PASS();
}

void test_learn_neighbor_self_ignored(void) {
    TEST(learn_neighbor_self_ignored);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 42;

    wf_learn_neighbor(&state, 42, 0.9f, 3);
    assert(state.neighbor_count == 0);
    assert(state.route_count == 0);

    wf_learn_neighbor(&state, 0, 0.9f, 3);
    assert(state.neighbor_count == 0);

    PASS();
}

void test_learn_neighbor_eviction(void) {
    TEST(learn_neighbor_eviction);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Fill all neighbor slots with low quality
    for (uint8_t i = 0; i < WF_MAX_NEIGHBORS; i++) {
        wf_learn_neighbor(&state, 100 + i, 0.1f, 1);
    }
    assert(state.neighbor_count == WF_MAX_NEIGHBORS);

    // Add a better neighbor — should evict worst
    wf_learn_neighbor(&state, 999, 0.99f, 5);
    assert(state.neighbor_count == WF_MAX_NEIGHBORS);

    // Check that 999 is in the table
    bool found = false;
    for (uint8_t i = 0; i < state.neighbor_count; i++) {
        if (state.neighbors[i].node_id == 999) { found = true; break; }
    }
    assert(found);

    PASS();
}

// ── Route Learning from Packets ────────────────────────────────

void test_learn_from_packet_single(void) {
    TEST(learn_from_packet_single);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Path: [A] — A is direct neighbor (1 hop)
    uint32_t path[1] = { 0xAAAA };
    wf_learn_from_packet(&state, path, 1, 0.9f);

    assert(state.route_count == 1);
    assert(state.routes[0].dest_id == 0xAAAA);
    assert(state.routes[0].next_hop == 0xAAAA);
    assert(state.routes[0].hop_count == 1);

    PASS();
}

void test_learn_from_packet_multi(void) {
    TEST(learn_from_packet_multi);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Path: [A, B, C] — A originated, B relayed, C relayed to us
    // We learn: A via C (3 hops), B via C (2 hops), C direct (1 hop)
    uint32_t path[3] = { 0xAAAA, 0xBBBB, 0xCCCC };
    wf_learn_from_packet(&state, path, 3, 0.8f);

    assert(state.route_count == 3);

    // Check route to A
    uint32_t next_a = wf_get_next_hop(&state, 0xAAAA);
    assert(next_a == 0xCCCC); // via C (last relay)

    // Check route to C
    uint32_t next_c = wf_get_next_hop(&state, 0xCCCC);
    assert(next_c == 0xCCCC); // direct

    PASS();
}

void test_learn_from_packet_update_better(void) {
    TEST(learn_from_packet_update_better);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // First: learn A via B (2 hops)
    uint32_t path1[2] = { 0xAAAA, 0xBBBB };
    wf_learn_from_packet(&state, path1, 2, 0.5f);
    assert(wf_get_next_hop(&state, 0xAAAA) == 0xBBBB);

    // Then: learn A directly (1 hop, better!)
    uint32_t path2[1] = { 0xAAAA };
    wf_learn_from_packet(&state, path2, 1, 0.9f);

    // Should update to direct route
    const wf_route_entry_t *r = wf_get_route(&state, 0xAAAA);
    assert(r != NULL);
    assert(r->hop_count == 1);
    assert(r->next_hop == 0xAAAA);

    PASS();
}

// ── Route Lookup ───────────────────────────────────────────────

void test_get_next_hop_unknown(void) {
    TEST(get_next_hop_unknown);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    assert(wf_get_next_hop(&state, 0xDEAD) == 0);
    PASS();
}

void test_get_next_hop_known(void) {
    TEST(get_next_hop_known);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 3);
    assert(wf_get_next_hop(&state, 10) == 10);

    PASS();
}

// ── Walk Scoring ───────────────────────────────────────────────

void test_walk_score_direct_dest(void) {
    TEST(walk_score_direct_dest);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 3);
    // Scoring neighbor 10 to reach dest 10 — should be very high
    float score = wf_walk_score(&state, 10, 10);
    assert(score >= 10000.0f);

    PASS();
}

void test_walk_score_with_route(void) {
    TEST(walk_score_with_route);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 5);
    wf_learn_neighbor(&state, 20, 0.5f, 2);

    // Learn: dest 99 reachable via neighbor 10
    // We simulate this by directly adding a route
    uint32_t path[2] = { 99, 10 };
    wf_learn_from_packet(&state, path, 2, 0.8f);

    float score_10 = wf_walk_score(&state, 10, 99);
    float score_20 = wf_walk_score(&state, 20, 99);

    // Neighbor 10 has route to 99 → should score much higher
    assert(score_10 > score_20);
    assert(score_10 > 1000.0f); // has_route bonus

    PASS();
}

void test_walk_score_unknown_neighbor(void) {
    TEST(walk_score_unknown_neighbor);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    float score = wf_walk_score(&state, 99, 50);
    assert(score < 0.0f); // unknown neighbor

    PASS();
}

void test_get_best_walkers(void) {
    TEST(get_best_walkers);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 5);
    wf_learn_neighbor(&state, 20, 0.3f, 1);
    wf_learn_neighbor(&state, 30, 0.7f, 3);

    wf_walk_score_t best[2];
    uint8_t n = wf_get_best_walkers(&state, 50, best, 2);
    assert(n == 2);

    // Best should be neighbor 10 (highest quality + degree)
    assert(best[0].neighbor_id == 10);
    assert(best[0].score > best[1].score);

    PASS();
}

// ── Route Expiry ───────────────────────────────────────────────

void test_expire_routes(void) {
    TEST(expire_routes);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Add a route with old timestamp
    wf_learn_neighbor(&state, 10, 0.9f, 3);
    assert(state.route_count == 1);

    // Set last_seen to 0 (boot time) and expire at 6 minutes
    state.routes[0].last_seen = 0;

    // 6 minutes later — should expire
    wf_expire_routes(&state, 360000); // 360 seconds > 300 seconds expiry
    assert(state.route_count == 0);

    PASS();
}

void test_expire_keeps_fresh(void) {
    TEST(expire_keeps_fresh);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_learn_neighbor(&state, 10, 0.9f, 3);
    // Set last_seen to "now"
    state.routes[0].last_seen = (uint16_t)(60000 / 1000); // 60 seconds

    // Expire at 2 minutes — route is only 1 minute old, should survive
    wf_expire_routes(&state, 120000);
    assert(state.route_count == 1);

    PASS();
}

// ── MPR Tests ──────────────────────────────────────────────────

void test_mpr_empty(void) {
    TEST(mpr_empty);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    wf_compute_mpr_set(&state);
    assert(state.mpr_count == 0);

    PASS();
}

void test_mpr_selects_high_degree(void) {
    TEST(mpr_selects_high_degree);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Add neighbors with varying degrees
    wf_learn_neighbor(&state, 10, 0.9f, 8); // high degree
    wf_learn_neighbor(&state, 20, 0.9f, 2); // medium degree
    wf_learn_neighbor(&state, 30, 0.9f, 0); // leaf node (no other connections)

    wf_compute_mpr_set(&state);

    // High-degree neighbor should be first MPR
    assert(state.mpr_count >= 1);
    assert(state.mpr_set[0] == 10);

    // Check is_mpr
    assert(wf_is_mpr(&state, 10) == true);

    PASS();
}

void test_mpr_is_mpr_false_for_non_mpr(void) {
    TEST(mpr_is_mpr_false);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    assert(wf_is_mpr(&state, 999) == false);

    PASS();
}

// ── Integration: Walk → Mini-flood → Flood ─────────────────────

void test_routing_decision_flow(void) {
    TEST(routing_decision_flow);
    wf_state_t state;
    wf_init(&state);
    state.my_id = 1;

    // Case 1: Unknown destination → no next hop (caller should flood)
    assert(wf_get_next_hop(&state, 0xDEAD) == 0);

    // Case 2: Learn a neighbor and route
    wf_learn_neighbor(&state, 10, 0.9f, 5);
    uint32_t path[2] = { 0xDEAD, 10 };
    wf_learn_from_packet(&state, path, 2, 0.8f);

    // Now we should have a route: dest DEAD via 10
    assert(wf_get_next_hop(&state, 0xDEAD) == 10);

    // Case 3: Best walkers should include node 10
    wf_walk_score_t best[2];
    uint8_t n = wf_get_best_walkers(&state, 0xDEAD, best, 2);
    assert(n >= 1);
    assert(best[0].neighbor_id == 10);

    PASS();
}

// ── Main ───────────────────────────────────────────────────────

int main(void) {
    printf("\n=== WalkFlood Routing — Unit Tests ===\n\n");

    test_init();
    test_learn_neighbor();
    test_learn_neighbor_update();
    test_learn_neighbor_self_ignored();
    test_learn_neighbor_eviction();
    test_learn_from_packet_single();
    test_learn_from_packet_multi();
    test_learn_from_packet_update_better();
    test_get_next_hop_unknown();
    test_get_next_hop_known();
    test_walk_score_direct_dest();
    test_walk_score_with_route();
    test_walk_score_unknown_neighbor();
    test_get_best_walkers();
    test_expire_routes();
    test_expire_keeps_fresh();
    test_mpr_empty();
    test_mpr_selects_high_degree();
    test_mpr_is_mpr_false_for_non_mpr();
    test_routing_decision_flow();

    printf("\n=== ALL %d TESTS PASSED ===\n\n", 20);
    return 0;
}
