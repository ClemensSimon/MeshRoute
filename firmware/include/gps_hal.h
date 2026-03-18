/**
 * MeshRoute System 5 — GPS / Position Abstraction
 *
 * Handles:
 * 1. Real GPS (T-Beam, RAK4631 with RAK1910)
 * 2. RSSI-based triangulation (no GPS hardware)
 * 3. Manual/fixed position (configured by user)
 * 4. Cluster inheritance (copy nearest neighbor's cluster)
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "system5.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    POS_SOURCE_NONE,         // no position yet
    POS_SOURCE_GPS,          // real GPS fix
    POS_SOURCE_MANUAL,       // user-configured fixed position
    POS_SOURCE_TRIANGULATED, // estimated from RSSI of known neighbors
    POS_SOURCE_INHERITED,    // copied cluster from strongest neighbor
} pos_source_t;

typedef struct {
    float lat;
    float lon;
    float accuracy_m;        // estimated accuracy in meters
    pos_source_t source;
    uint32_t fix_time_ms;    // millis() when position was determined
    bool valid;
} position_t;

/**
 * Initialize GPS hardware (if available).
 */
void gps_init(void);

/**
 * Update GPS reading. Call frequently in loop().
 * @return true if a new fix is available
 */
bool gps_update(void);

/**
 * Get current position (from whatever source is available).
 */
position_t gps_get_position(void);

/**
 * Set a manual/fixed position (for nodes without GPS).
 */
void gps_set_manual(float lat, float lon);

/**
 * Estimate position via RSSI triangulation.
 * Requires at least 3 neighbors with known positions.
 *
 * @param state   Node state with neighbor table
 * @param out     Output position
 * @return true if triangulation succeeded
 */
bool gps_triangulate(const s5_node_state_t *state, position_t *out);

/**
 * Inherit cluster from the strongest neighbor.
 * Fallback when no position is available at all.
 *
 * @param state   Node state with neighbor table
 * @return cluster_id of strongest neighbor, or 0xFF if none
 */
uint8_t gps_inherit_cluster(const s5_node_state_t *state);

#ifdef __cplusplus
}
#endif
