/**
 * Unit tests for System 5 core logic.
 * Can run on native platform (no ESP32 needed).
 *
 * Build: pio test -e native
 * Or: gcc -I../include -lm -o test_system5 test_system5.c ../src/system5.c && ./test_system5
 */

#include "system5.h"
#include <stdio.h>
#include <assert.h>
#include <string.h>
#include <math.h>

#define TEST(name) printf("  TEST: %s ... ", #name)
#define PASS() printf("PASS\n")

// ── Geohash Tests ──────────────────────────────────────────────

void test_geohash_encode() {
    TEST(geohash_encode);
    s5_geohash_t gh;

    // Munich area: 48.1, 11.5
    s5_geohash_encode(48.1f, 11.5f, 4, &gh);
    assert(gh.hash[0] != '\0');
    assert(strlen(gh.hash) == 4);

    // Two close points should share prefix
    s5_geohash_t gh2;
    s5_geohash_encode(48.1001f, 11.5001f, 4, &gh2);
    uint8_t common = s5_geohash_common_prefix(&gh, &gh2);
    assert(common >= 3); // very close points share 3+ chars

    // Two far points should differ early
    s5_geohash_t gh3;
    s5_geohash_encode(-33.8f, 151.2f, 4, &gh3); // Sydney
    common = s5_geohash_common_prefix(&gh, &gh3);
    assert(common <= 1);

    PASS();
}

// ── Node State Tests ───────────────────────────────────────────

void test_init() {
    TEST(init);
    s5_node_state_t state;
    s5_init(&state);
    assert(state.my_cluster_id == 0xFF);
    assert(state.neighbor_count == 0);
    assert(state.my_is_border == false);
    PASS();
}

void test_update_position() {
    TEST(update_position);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;

    s5_update_position(&state, 48.1f, 11.5f);
    assert(state.my_geohash.hash[0] != '\0');
    assert(state.my_cluster_id != 0xFF);
    PASS();
}

// ── Neighbor Tests ─────────────────────────────────────────────

void test_add_neighbor() {
    TEST(add_neighbor);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    // Add a close neighbor (same cluster)
    s5_update_neighbor(&state, 2, 48.1001f, 11.5001f, 80, -5, 0.9f);
    assert(state.neighbor_count == 1);
    assert(state.neighbors[0].id == 2);
    assert(state.neighbors[0].link_quality > 0.8f);

    // Add a far neighbor (different cluster)
    s5_update_neighbor(&state, 3, 49.0f, 12.0f, 70, -10, 0.5f);
    assert(state.neighbor_count == 2);

    // We should now be a border node (neighbors in different clusters)
    assert(state.my_is_border == true);

    PASS();
}

void test_remove_neighbor() {
    TEST(remove_neighbor);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    s5_update_neighbor(&state, 2, 48.1001f, 11.5001f, 80, -5, 0.9f);
    s5_update_neighbor(&state, 3, 48.1002f, 11.5002f, 70, -8, 0.7f);
    assert(state.neighbor_count == 2);

    s5_remove_neighbor(&state, 2);
    assert(state.neighbor_count == 1);
    assert(state.neighbors[0].id == 3);

    PASS();
}

void test_neighbor_eviction() {
    TEST(neighbor_eviction);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    // Fill all neighbor slots
    for (uint8_t i = 0; i < S5_MAX_NEIGHBORS; i++) {
        s5_update_neighbor(&state, 100 + i, 48.1f + i * 0.001f, 11.5f, 80, -5, 0.1f + i * 0.05f);
    }
    assert(state.neighbor_count == S5_MAX_NEIGHBORS);

    // Add better neighbor — should evict the worst (lowest quality)
    s5_update_neighbor(&state, 999, 48.11f, 11.51f, 90, -3, 0.99f);
    assert(state.neighbor_count == S5_MAX_NEIGHBORS);

    // Check that node 999 is now in the table
    bool found = false;
    for (uint8_t i = 0; i < state.neighbor_count; i++) {
        if (state.neighbors[i].id == 999) { found = true; break; }
    }
    assert(found);

    PASS();
}

// ── Routing Tests ──────────────────────────────────────────────

void test_route_self() {
    TEST(route_self);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 42;

    s5_packet_t pkt = {
        .src = 1, .dst = 42, .packet_id = 100,
        .priority = 3, .hop_count = 2, .ttl = 10,
        .next_hop = 0, .is_system5 = false,
        .payload_len = 0, .payload = NULL,
    };

    s5_route_decision_t d = s5_route(&state, &pkt);
    assert(d.action == S5_ROUTE_DELIVERED);
    PASS();
}

void test_route_no_route_floods() {
    TEST(route_no_route_floods);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    // Add a neighbor so NHS > 0
    s5_update_neighbor(&state, 2, 48.1001f, 11.5001f, 80, -5, 0.9f);

    s5_packet_t pkt = {
        .src = 1, .dst = 99, .packet_id = 101,
        .priority = 3, .hop_count = 0, .ttl = 10,
        .next_hop = 0, .is_system5 = false,
        .payload_len = 0, .payload = NULL,
    };

    s5_route_decision_t d = s5_route(&state, &pkt);
    // No route in table — should fall back to flooding
    assert(d.action == S5_ROUTE_FLOOD);
    assert(d.used_fallback == true);
    PASS();
}

void test_route_ttl_expired() {
    TEST(route_ttl_expired);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);
    s5_update_neighbor(&state, 2, 48.1001f, 11.5001f, 80, -5, 0.9f);

    s5_packet_t pkt = {
        .src = 5, .dst = 99, .packet_id = 102,
        .priority = 3, .hop_count = 10, .ttl = 10, // hop == ttl
        .next_hop = 0, .is_system5 = false,
        .payload_len = 0, .payload = NULL,
    };

    s5_route_decision_t d = s5_route(&state, &pkt);
    assert(d.action == S5_ROUTE_DROP);
    PASS();
}

// ── NHS Tests ──────────────────────────────────────────────────

void test_nhs_empty() {
    TEST(nhs_empty);
    s5_node_state_t state;
    s5_init(&state);
    float nhs = s5_get_nhs(&state);
    assert(nhs == 0.0f);
    PASS();
}

void test_nhs_healthy() {
    TEST(nhs_healthy);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    // Add 3 good neighbors in same cluster
    for (int i = 0; i < 3; i++) {
        s5_update_neighbor(&state, 10 + i, 48.1f + i * 0.0001f, 11.5f + i * 0.0001f,
                           90, -3, 0.95f);
    }

    float nhs = s5_get_nhs(&state);
    assert(nhs > 0.7f); // should be healthy
    PASS();
}

// ── Adaptive Retry Tests ──────────────────────────────────────

void test_adaptive_retries() {
    TEST(adaptive_retries);
    // Good link (>0.5) should get fewer retries
    assert(s5_get_retry_count(0.9f) == S5_MAX_RETRIES);
    assert(s5_get_retry_count(0.6f) == S5_MAX_RETRIES);
    // Poor link (<=0.5) should get more retries
    assert(s5_get_retry_count(0.5f) == S5_MAX_RETRIES_POOR);
    assert(s5_get_retry_count(0.1f) == S5_MAX_RETRIES_POOR);
    PASS();
}

// ── Dynamic Max Hops Tests ────────────────────────────────────

void test_dynamic_max_hops() {
    TEST(dynamic_max_hops);
    // 0 nodes → floor
    assert(s5_dynamic_max_hops(0) == S5_MIN_HOPS);
    // Small network (25 nodes): sqrt(25)*3 = 15
    assert(s5_dynamic_max_hops(25) == S5_MIN_HOPS);
    // Medium network (100 nodes): sqrt(100)*3 = 30
    assert(s5_dynamic_max_hops(100) == 30);
    // Large (255 max uint8): sqrt(255)*3 ≈ 47.9 → capped at 40
    assert(s5_dynamic_max_hops(255) == S5_MAX_HOPS_CAP);
    PASS();
}

// ── Flood Corridor Tests ──────────────────────────────────────

void test_flood_corridor_same_cluster() {
    TEST(flood_corridor_same_cluster);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;
    s5_update_position(&state, 48.1f, 11.5f);

    uint8_t corridor[8];
    uint8_t len = s5_get_flood_corridor(&state, state.my_cluster_id, state.my_cluster_id, corridor, 8);
    assert(len == 1);
    assert(corridor[0] == state.my_cluster_id);
    PASS();
}

// ── Probe Tests ──────────────────────────────────────────────

void test_probe_no_secondary_routes() {
    TEST(probe_no_secondary_routes);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;

    s5_node_id_t dest; s5_node_id_t next; uint8_t ridx;
    // No routes at all — should return false
    bool should_probe = s5_pick_probe_target(&state, 60000, &dest, &next, &ridx);
    assert(!should_probe);
    PASS();
}

void test_probe_reply_success() {
    TEST(probe_reply_success);
    s5_node_state_t state;
    s5_init(&state);
    state.my_id = 1;

    // Simulate a successful probe reply — quality should improve
    // We need a route entry in the table for this to work
    // Since _route_table is static, we test via the public API indirectly
    // by calling s5_handle_probe_reply (it should not crash with no entry)
    s5_handle_probe_reply(&state, 99, 0, 500, true);
    s5_handle_probe_reply(&state, 99, 0, 500, false);
    PASS();
}

// ── Main ───────────────────────────────────────────────────────

int main() {
    printf("\n=== MeshRoute System 5 — Unit Tests ===\n\n");

    test_geohash_encode();
    test_init();
    test_update_position();
    test_add_neighbor();
    test_remove_neighbor();
    test_neighbor_eviction();
    test_route_self();
    test_route_no_route_floods();
    test_route_ttl_expired();
    test_nhs_empty();
    test_nhs_healthy();
    test_adaptive_retries();
    test_dynamic_max_hops();
    test_flood_corridor_same_cluster();
    test_probe_no_secondary_routes();
    test_probe_reply_success();

    printf("\n=== ALL TESTS PASSED ===\n\n");
    return 0;
}
