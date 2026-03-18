/**
 * MeshRoute System 5 — GPS / Position Implementation
 */

#include "gps_hal.h"
#include "board_config.h"

#if HAS_GPS
#include <TinyGPSPlus.h>
static TinyGPSPlus gps;
#if defined(ESP32) || defined(ESP_PLATFORM)
  #include <HardwareSerial.h>
  static HardwareSerial gpsSerial(1);
#elif defined(NRF52_SERIES) || defined(ARDUINO_ARCH_NRF52)
  // RAK4631: GPS on UART1 (Serial1)
  #define gpsSerial Serial1
#endif
#endif

static position_t currentPos = {0, 0, 0, POS_SOURCE_NONE, 0, false};

// ── Init ───────────────────────────────────────────────────────

void gps_init(void) {
#if HAS_GPS && GPS_RX >= 0
    gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);
    Serial.println("[GPS] UART initialized");
#else
    Serial.println("[GPS] No GPS hardware — using triangulation/manual");
#endif
}

// ── Update ─────────────────────────────────────────────────────

bool gps_update(void) {
#if HAS_GPS && GPS_RX >= 0
    bool newFix = false;
    while (gpsSerial.available()) {
        char c = gpsSerial.read();
        if (gps.encode(c)) {
            if (gps.location.isValid() && gps.location.isUpdated()) {
                currentPos.lat = (float)gps.location.lat();
                currentPos.lon = (float)gps.location.lng();
                currentPos.accuracy_m = (float)gps.hdop.hdop() * 5.0f; // rough estimate
                currentPos.source = POS_SOURCE_GPS;
                currentPos.fix_time_ms = millis();
                currentPos.valid = true;
                newFix = true;
            }
        }
    }
    return newFix;
#else
    return false;
#endif
}

// ── Get Position ───────────────────────────────────────────────

position_t gps_get_position(void) {
    return currentPos;
}

// ── Manual Position ────────────────────────────────────────────

void gps_set_manual(float lat, float lon) {
    currentPos.lat = lat;
    currentPos.lon = lon;
    currentPos.accuracy_m = 50.0f; // user-configured = ~50m accuracy assumed
    currentPos.source = POS_SOURCE_MANUAL;
    currentPos.fix_time_ms = millis();
    currentPos.valid = true;
    Serial.printf("[GPS] Manual position set: %.6f, %.6f\n", lat, lon);
}

// ── RSSI Triangulation ─────────────────────────────────────────

// Estimate distance from RSSI using log-distance path loss model
static float _rssi_to_distance(int8_t snr) {
    // Simplified: SNR to approximate distance in meters
    // SNR 10 dB ≈ 100m, SNR -10 dB ≈ 5000m (EU868 urban)
    float snr_f = (float)snr;
    if (snr_f > 10.0f) return 50.0f;
    if (snr_f < -15.0f) return 10000.0f;

    // Log model: d = d0 * 10^((SNR0 - SNR) / (10 * n))
    // d0=100m, SNR0=10dB, n=2.8 (urban)
    float exponent = (10.0f - snr_f) / (10.0f * 2.8f);
    return 100.0f * powf(10.0f, exponent);
}

bool gps_triangulate(const s5_node_state_t *state, position_t *out) {
    // Need at least 3 neighbors with known positions
    float lats[S5_MAX_NEIGHBORS], lons[S5_MAX_NEIGHBORS], weights[S5_MAX_NEIGHBORS];
    int count = 0;

    for (uint8_t i = 0; i < state->neighbor_count && count < S5_MAX_NEIGHBORS; i++) {
        const s5_neighbor_t *n = &state->neighbors[i];
        if (n->lat == 0.0f && n->lon == 0.0f) continue; // no position known
        float dist = _rssi_to_distance(n->snr);
        if (dist < 1.0f) dist = 1.0f;
        lats[count] = n->lat;
        lons[count] = n->lon;
        weights[count] = 1.0f / (dist * dist); // inverse square weighting
        count++;
    }

    if (count < 3) return false;

    // Weighted centroid (not true trilateration, but good enough for clustering)
    float total_w = 0, avg_lat = 0, avg_lon = 0;
    for (int i = 0; i < count; i++) {
        avg_lat += lats[i] * weights[i];
        avg_lon += lons[i] * weights[i];
        total_w += weights[i];
    }

    out->lat = avg_lat / total_w;
    out->lon = avg_lon / total_w;
    out->accuracy_m = 500.0f; // rough estimate
    out->source = POS_SOURCE_TRIANGULATED;
    out->fix_time_ms = millis();
    out->valid = true;

    Serial.printf("[GPS] Triangulated: %.6f, %.6f (from %d neighbors)\n",
                  out->lat, out->lon, count);
    return true;
}

// ── Cluster Inheritance ────────────────────────────────────────

uint8_t gps_inherit_cluster(const s5_node_state_t *state) {
    if (state->neighbor_count == 0) return 0xFF;

    // Find neighbor with best link quality
    uint8_t best = 0;
    float best_q = state->neighbors[0].link_quality;
    for (uint8_t i = 1; i < state->neighbor_count; i++) {
        if (state->neighbors[i].link_quality > best_q) {
            best_q = state->neighbors[i].link_quality;
            best = i;
        }
    }
    Serial.printf("[GPS] Inheriting cluster %u from neighbor %u (quality %.2f)\n",
                  state->neighbors[best].cluster_id, state->neighbors[best].id, best_q);
    return state->neighbors[best].cluster_id;
}
