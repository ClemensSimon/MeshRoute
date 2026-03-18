/**
 * MeshRoute System 5 — Wire Protocol Implementation
 */

#include "wire_protocol.h"
#include <string.h>

_Static_assert(sizeof(s5_wire_header_t) == 22, "wire header must be 22 bytes packed");

static uint32_t _next_packet_id = 0;

// Call once at boot to seed packet IDs uniquely per node
void s5_wire_seed_packet_id(uint32_t node_id, uint32_t boot_time) {
    _next_packet_id = node_id ^ boot_time ^ 0xA5A5;
}

uint8_t s5_wire_pack(const s5_wire_header_t *hdr, const uint8_t *payload,
                      uint8_t *out_buf, uint8_t max_len) {
    uint8_t total = sizeof(s5_wire_header_t) + hdr->payload_len;
    if (total > max_len) return 0;

    memcpy(out_buf, hdr, sizeof(s5_wire_header_t));
    if (payload && hdr->payload_len > 0) {
        memcpy(out_buf + sizeof(s5_wire_header_t), payload, hdr->payload_len);
    }
    return total;
}

bool s5_wire_unpack(const uint8_t *buf, uint8_t len,
                    s5_wire_header_t *hdr, const uint8_t **payload) {
    if (len < sizeof(s5_wire_header_t)) return false;

    memcpy(hdr, buf, sizeof(s5_wire_header_t));
    if (hdr->magic != S5_MAGIC) return false;
    if (sizeof(s5_wire_header_t) + hdr->payload_len > len) return false;

    *payload = buf + sizeof(s5_wire_header_t);
    return true;
}

uint8_t s5_create_ogm(const s5_node_state_t *state, float lat, float lon,
                       uint8_t pos_source, uint8_t *out_buf, uint8_t max_len) {
    s5_ogm_payload_t ogm = {
        .lat = lat,
        .lon = lon,
        .battery_pct = state->my_battery_pct,
        .cluster_id = state->my_cluster_id,
        .is_border = state->my_is_border ? 1 : 0,
        .neighbor_count = state->neighbor_count,
        .pos_source = pos_source,
    };

    s5_wire_header_t hdr = {
        .magic = S5_MAGIC,
        .type = PKT_TYPE_OGM,
        .src = state->my_id,
        .dst = S5_BROADCAST,
        .packet_id = _next_packet_id++,
        .hop_count = 0,
        .ttl = 3, // OGMs don't need many hops
        .next_hop = 0,
        .priority = 7, // lowest priority for maintenance
        .payload_len = sizeof(s5_ogm_payload_t),
    };

    return s5_wire_pack(&hdr, (const uint8_t *)&ogm, out_buf, max_len);
}

uint8_t s5_create_data(const s5_node_state_t *state, s5_node_id_t dst,
                        uint32_t next_hop, uint8_t priority,
                        const uint8_t *payload, uint8_t payload_len,
                        uint8_t *out_buf, uint8_t max_len) {
    s5_wire_header_t hdr = {
        .magic = S5_MAGIC,
        .type = PKT_TYPE_DATA,
        .src = state->my_id,
        .dst = dst,
        .packet_id = _next_packet_id++,
        .hop_count = 0,
        .ttl = S5_MAX_HOPS,
        .next_hop = next_hop,
        .priority = priority,
        .payload_len = payload_len,
    };

    return s5_wire_pack(&hdr, payload, out_buf, max_len);
}
