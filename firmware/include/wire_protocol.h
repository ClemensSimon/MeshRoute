/**
 * MeshRoute System 5 — Wire Protocol
 *
 * Over-the-air packet format for System 5 routing.
 * Designed to coexist with Meshtastic (different sync word).
 *
 * Packet structure (max 256 bytes):
 * [Header: 24 bytes] [Payload: 0-232 bytes]
 *
 * v2.0: Added seq (2 bytes) for per-(src,dst) gap detection
 *       Added PKT_TYPE_SILENCE for node silencing control
 */

#pragma once

#include <stdint.h>
#include "system5.h"

#ifdef __cplusplus
extern "C" {
#endif

// Packet types
#define PKT_TYPE_OGM      0x01  // Originator Message (neighbor discovery)
#define PKT_TYPE_DATA     0x02  // User data (routed)
#define PKT_TYPE_ACK      0x03  // Acknowledgement
#define PKT_TYPE_ROUTE_REQ 0x04 // Route request (on-demand)
#define PKT_TYPE_SILENCE  0x05  // Node silencing control (mute/unmute)
#define PKT_TYPE_PROBE    0x06  // Route probe (keepalive for secondary routes)

// ── Header (24 bytes, unencrypted) ─────────────────────────────

typedef struct __attribute__((packed)) {
    uint8_t  magic;          // 0x55 = System 5 packet
    uint8_t  type;           // PKT_TYPE_*
    uint32_t src;            // originator node ID
    uint32_t dst;            // destination (0xFFFFFFFF = broadcast)
    uint32_t packet_id;      // unique ID (for dedup)
    uint8_t  hop_count;      // hops so far
    uint8_t  ttl;            // remaining hops allowed
    uint32_t next_hop;       // System 5: directed next hop (0 = flood)
    uint8_t  priority;       // QoS 0-7
    uint16_t seq;            // per-(src,dst) sequence number for gap detection
    uint8_t  payload_len;    // length of payload after header
} s5_wire_header_t;

#define S5_MAGIC 0x55        // 'U' for "unicast/unified"
#define S5_BROADCAST 0xFFFFFFFF

// ── OGM Payload ────────────────────────────────────────────────
// Sent periodically by every node. Contains position + status.

typedef struct __attribute__((packed)) {
    float    lat;            // GPS latitude
    float    lon;            // GPS longitude
    uint8_t  battery_pct;    // 0-100
    uint8_t  cluster_id;     // node's cluster
    uint8_t  is_border;      // 1 if border node
    uint8_t  neighbor_count; // how many neighbors this node has
    uint8_t  pos_source;     // pos_source_t: GPS, manual, triangulated, inherited
    uint8_t  is_silent;      // 1 if node is currently silenced (listens only)
} s5_ogm_payload_t;

// ── Silence Control Payload ───────────────────────────────────
// Sent by cluster coordinators to mute/unmute redundant nodes.

typedef struct __attribute__((packed)) {
    uint32_t target_node;    // node to silence/unsilence
    uint16_t duration_sec;   // silence duration (0 = unsilence)
    uint8_t  reason;         // 0=redundancy, 1=battery_save, 2=manual
} s5_silence_payload_t;

// ── Probe Payload ─────────────────────────────────────────────
// Minimal keepalive sent along secondary routes to verify liveness.
// 10 bytes total: target destination + route index + timestamp.

typedef struct __attribute__((packed)) {
    uint32_t probe_dst;      // final destination of the route being probed
    uint8_t  route_index;    // which cached route (0-4)
    uint8_t  probe_flags;    // 0x01 = request echo, 0x02 = echo reply
    uint32_t sent_ms;        // sender's timestamp (for RTT measurement)
} s5_probe_payload_t;

#define S5_PROBE_FLAG_REQUEST  0x01
#define S5_PROBE_FLAG_REPLY    0x02

// ── Helper Functions ───────────────────────────────────────────

/**
 * Serialize a header + payload into a wire buffer.
 * @return total length (header + payload)
 */
/**
 * Seed packet ID generator (call once at boot with unique values).
 */
void s5_wire_seed_packet_id(uint32_t node_id, uint32_t boot_time);

uint8_t s5_wire_pack(const s5_wire_header_t *hdr, const uint8_t *payload,
                      uint8_t *out_buf, uint8_t max_len);

/**
 * Parse a received wire buffer into header + payload pointer.
 * @return true if valid packet
 */
bool s5_wire_unpack(const uint8_t *buf, uint8_t len,
                    s5_wire_header_t *hdr, const uint8_t **payload);

/**
 * Create an OGM packet from current node state.
 */
uint8_t s5_create_ogm(const s5_node_state_t *state, float lat, float lon,
                       uint8_t pos_source, uint8_t *out_buf, uint8_t max_len);

/**
 * Create a data packet with System 5 routing.
 */
uint8_t s5_create_data(const s5_node_state_t *state, s5_node_id_t dst,
                        uint32_t next_hop, uint8_t priority,
                        const uint8_t *payload, uint8_t payload_len,
                        uint8_t *out_buf, uint8_t max_len);

/**
 * Create a route probe packet along a secondary route.
 * Minimal payload (10 bytes) to verify route liveness.
 */
uint8_t s5_create_probe(const s5_node_state_t *state, s5_node_id_t probe_dst,
                          uint32_t next_hop, uint8_t route_index,
                          uint32_t now_ms, uint8_t flags,
                          uint8_t *out_buf, uint8_t max_len);

#ifdef __cplusplus
}
#endif
