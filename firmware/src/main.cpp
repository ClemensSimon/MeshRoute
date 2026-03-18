/**
 * MeshRoute System 5 — Main Application
 *
 * Standalone LoRa mesh node with System 5 routing.
 * Supports: Heltec V3, T-Beam, RAK4631
 *
 * What it does:
 * 1. Boots, initializes LoRa + GPS (if available)
 * 2. Sends OGM every 30s to discover neighbors
 * 3. Builds routing table from received OGMs
 * 4. Routes incoming data packets via System 5 (directed) or flood (fallback)
 * 5. Displays status on OLED (if available)
 * 6. Accepts serial commands to send test messages
 */

#include <Arduino.h>
#include "board_config.h"
#include "system5.h"
#include "lora_hal.h"
#include "gps_hal.h"
#include "wire_protocol.h"

#if HAS_DISPLAY
#include <Wire.h>
#include <U8g2lib.h>
#if defined(BOARD_HELTEC_V3)
  static U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, OLED_RST, OLED_SCL, OLED_SDA);
#elif defined(BOARD_TBEAM)
  static U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, OLED_RST);
#endif
#endif

#if defined(HAS_AXP192) && HAS_AXP192
#include <axp20x.h>
static AXP20X_Class axp;
#endif

// ── State ──────────────────────────────────────────────────────

static s5_node_state_t nodeState;
static uint8_t txBuf[LORA_MAX_PACKET_SIZE];
static uint8_t rxBuf[LORA_MAX_PACKET_SIZE];
static uint32_t lastOGM = 0;
static uint32_t lastMaintenance = 0;
static uint32_t lastDisplay = 0;
static uint32_t packetsRx = 0;
static uint32_t packetsTx = 0;
static uint32_t packetsRouted = 0;

// Dedup ring buffer (last 64 packet IDs)
#define DEDUP_SIZE 64
static uint32_t dedupRing[DEDUP_SIZE];
static uint8_t dedupIdx = 0;

static bool isDuplicate(uint32_t pkt_id) {
    for (uint8_t i = 0; i < DEDUP_SIZE; i++) {
        if (dedupRing[i] == pkt_id) return true;
    }
    dedupRing[dedupIdx] = pkt_id;
    dedupIdx = (dedupIdx + 1) % DEDUP_SIZE;
    return false;
}

// ── Generate unique node ID from chip ID ───────────────────────

static uint32_t getNodeId(void) {
#if defined(ESP32) || defined(ESP_PLATFORM)
    uint64_t mac = ESP.getEfuseMac();
    return (uint32_t)(mac & 0xFFFFFFFF);
#elif defined(NRF52_SERIES)
    return NRF_FICR->DEVICEADDR[0];
#else
    return 0x12345678;
#endif
}

// ── Handle received OGM ────────────────────────────────────────

static void handleOGM(const s5_wire_header_t *hdr, const uint8_t *payload,
                       int16_t rssi, int8_t snr) {
    if (hdr->payload_len < sizeof(s5_ogm_payload_t)) return;

    const s5_ogm_payload_t *ogm = (const s5_ogm_payload_t *)payload;

    // Compute link quality from RSSI (0-1)
    // RSSI -50 = excellent (1.0), RSSI -120 = unusable (0.0)
    float quality = ((float)rssi + 120.0f) / 70.0f;
    if (quality > 1.0f) quality = 1.0f;
    if (quality < 0.0f) quality = 0.0f;

    // Update neighbor table
    s5_update_neighbor(&nodeState, hdr->src, ogm->lat, ogm->lon,
                        ogm->battery_pct, snr, quality);

    // Update neighbor's last_heard
    for (uint8_t i = 0; i < nodeState.neighbor_count; i++) {
        if (nodeState.neighbors[i].id == hdr->src) {
            nodeState.neighbors[i].last_heard_ms = millis();
            break;
        }
    }

    Serial.printf("[OGM] From %08X: (%.4f,%.4f) batt=%u%% cluster=%u q=%.2f snr=%d\n",
                  hdr->src, ogm->lat, ogm->lon, ogm->battery_pct,
                  ogm->cluster_id, quality, snr);
}

// ── Handle received data packet ────────────────────────────────

static void handleData(const s5_wire_header_t *hdr, const uint8_t *payload,
                        int16_t rssi, int8_t snr) {
    // Build S5 packet struct for routing decision
    s5_packet_t pkt = {
        .src = hdr->src,
        .dst = hdr->dst,
        .packet_id = hdr->packet_id,
        .priority = hdr->priority,
        .hop_count = hdr->hop_count,
        .ttl = hdr->ttl,
        .next_hop = hdr->next_hop,
        .is_system5 = (hdr->next_hop != 0),
        .payload_len = hdr->payload_len,
        .payload = NULL,
    };

    s5_route_decision_t decision = s5_route(&nodeState, &pkt);

    switch (decision.action) {
        case S5_ROUTE_DELIVERED:
            Serial.printf("[DATA] Received message from %08X: %.*s\n",
                          hdr->src, hdr->payload_len, payload);
            break;

        case S5_ROUTE_DIRECT: {
            Serial.printf("[DATA] Routing %08X->%08X via next_hop %08X\n",
                          hdr->src, hdr->dst, decision.next_hop);
            // Forward with updated hop count and next_hop
            s5_wire_header_t fwd = *hdr;
            fwd.hop_count++;
            fwd.ttl--;
            fwd.next_hop = decision.next_hop;
            uint8_t len = s5_wire_pack(&fwd, payload, txBuf, sizeof(txBuf));
            if (len > 0 && lora_send(txBuf, len)) {
                packetsTx++;
                packetsRouted++;
                s5_route_feedback(&nodeState, hdr->dst, decision.route_index, true);
            } else {
                s5_route_feedback(&nodeState, hdr->dst, decision.route_index, false);
            }
            break;
        }

        case S5_ROUTE_FLOOD: {
            Serial.printf("[DATA] Fallback flood %08X->%08X (hop %u)\n",
                          hdr->src, hdr->dst, hdr->hop_count);
            // Rebroadcast with incremented hop count, clear next_hop
            s5_wire_header_t fwd = *hdr;
            fwd.hop_count++;
            fwd.ttl--;
            fwd.next_hop = 0; // flood = no directed next hop
            uint8_t len = s5_wire_pack(&fwd, payload, txBuf, sizeof(txBuf));
            if (len > 0 && lora_send(txBuf, len)) {
                packetsTx++;
                packetsRouted++;
            }
            break;
        }

        case S5_ROUTE_DROP:
            Serial.printf("[DATA] Dropped %08X->%08X (QoS/TTL)\n",
                          hdr->src, hdr->dst);
            break;
    }
}

// ── Send OGM ───────────────────────────────────────────────────

static void sendOGM(void) {
    position_t pos = gps_get_position();

    // If no position, try triangulation first, then cluster inheritance
    if (!pos.valid && nodeState.neighbor_count >= 3) {
        position_t tri;
        if (gps_triangulate(&nodeState, &tri)) {
            pos = tri;
            s5_update_position(&nodeState, pos.lat, pos.lon);
        }
    }
    if (!pos.valid && nodeState.neighbor_count > 0) {
        nodeState.my_cluster_id = gps_inherit_cluster(&nodeState);
    }

    uint8_t len = s5_create_ogm(&nodeState, pos.lat, pos.lon,
                                  (uint8_t)pos.source, txBuf, sizeof(txBuf));
    if (len > 0 && lora_send(txBuf, len)) {
        packetsTx++;
        Serial.printf("[OGM] Sent (%u bytes, %u neighbors, cluster %u)\n",
                      len, nodeState.neighbor_count, nodeState.my_cluster_id);
    }
}

// ── Serial Command Handler ─────────────────────────────────────

static void handleSerial(void) {
    if (!Serial.available()) return;

    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("send ")) {
        // "send <dst_hex> <message>"
        // Example: "send FF001234 Hello World"
        uint32_t dst = strtoul(line.substring(5, 13).c_str(), NULL, 16);
        String msg = line.substring(14);

        // Route decision
        s5_packet_t pkt = {
            .src = nodeState.my_id, .dst = dst, .packet_id = millis(),
            .priority = 3, .hop_count = 0, .ttl = S5_MAX_HOPS,
            .next_hop = 0, .is_system5 = false,
            .payload_len = (uint16_t)msg.length(), .payload = NULL,
        };
        s5_route_decision_t d = s5_route(&nodeState, &pkt);

        uint32_t next = (d.action == S5_ROUTE_DIRECT) ? d.next_hop : 0;
        uint8_t len = s5_create_data(&nodeState, dst, next, 3,
                                      (const uint8_t *)msg.c_str(), msg.length(),
                                      txBuf, sizeof(txBuf));
        if (len > 0 && lora_send(txBuf, len)) {
            packetsTx++;
            Serial.printf("[TX] Sent to %08X via %s (%u bytes)\n",
                          dst, next ? "DIRECT" : "FLOOD", len);
        } else {
            Serial.println("[TX] FAILED");
        }

    } else if (line == "status") {
        Serial.printf("\n=== Node %08X (%s) ===\n", nodeState.my_id, BOARD_NAME);
        Serial.printf("Cluster: %u, Border: %s\n", nodeState.my_cluster_id,
                      nodeState.my_is_border ? "yes" : "no");
        position_t pos = gps_get_position();
        Serial.printf("Position: %.6f, %.6f (source: %u)\n", pos.lat, pos.lon, pos.source);
        Serial.printf("NHS: %.2f\n", s5_get_nhs(&nodeState));
        Serial.printf("Neighbors: %u\n", nodeState.neighbor_count);
        for (uint8_t i = 0; i < nodeState.neighbor_count; i++) {
            const s5_neighbor_t *n = &nodeState.neighbors[i];
            Serial.printf("  %08X: q=%.2f snr=%d batt=%u%% cluster=%u %s\n",
                          n->id, n->link_quality, n->snr, n->battery_pct,
                          n->cluster_id, n->is_border ? "[BORDER]" : "");
        }
        Serial.printf("Stats: TX=%u RX=%u Routed=%u\n\n", packetsTx, packetsRx, packetsRouted);

    } else if (line.startsWith("pos ")) {
        // "pos <lat> <lon>" — set manual position
        float lat = line.substring(4).toFloat();
        int comma = line.indexOf(' ', 5);
        float lon = line.substring(comma + 1).toFloat();
        gps_set_manual(lat, lon);
        s5_update_position(&nodeState, lat, lon);

    } else if (line == "help") {
        Serial.println("\nCommands:");
        Serial.println("  send <dst_hex> <message>  — Send message to node");
        Serial.println("  status                    — Show node status");
        Serial.println("  pos <lat> <lon>           — Set manual position");
        Serial.println("  help                      — This help\n");
    }
}

// ── Display ────────────────────────────────────────────────────

#if HAS_DISPLAY
static void updateDisplay(void) {
    display.clearBuffer();
    display.setFont(u8g2_font_6x10_tr);

    // Line 1: Node ID
    char line[32];
    snprintf(line, sizeof(line), "S5 %08X", nodeState.my_id);
    display.drawStr(0, 10, line);
    display.drawStr(90, 10, BOARD_NAME);

    // Line 2: Cluster + NHS
    snprintf(line, sizeof(line), "C%u %s NHS:%.1f",
             nodeState.my_cluster_id,
             nodeState.my_is_border ? "BRD" : "   ",
             s5_get_nhs(&nodeState));
    display.drawStr(0, 22, line);

    // Line 3: Neighbors
    snprintf(line, sizeof(line), "Neighbors: %u", nodeState.neighbor_count);
    display.drawStr(0, 34, line);

    // Line 4: Position source
    position_t pos = gps_get_position();
    const char *src_str[] = {"NONE", "GPS", "MANUAL", "TRIANG", "INHERIT"};
    snprintf(line, sizeof(line), "Pos: %s", pos.valid ? src_str[pos.source] : "NONE");
    display.drawStr(0, 46, line);

    // Line 5: Stats
    snprintf(line, sizeof(line), "TX:%u RX:%u R:%u", packetsTx, packetsRx, packetsRouted);
    display.drawStr(0, 58, line);

    display.sendBuffer();
}
#endif

// ── Setup ──────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.printf("\n\n=== MeshRoute System 5 v%s ===\n", S5_FIRMWARE_VERSION);
    Serial.printf("Board: %s\n", BOARD_NAME);

    // LED
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, HIGH);

#if defined(HAS_AXP192) && HAS_AXP192
    // T-Beam power management: enable GPS + LoRa power
    Wire.begin(21, 22);
    if (axp.begin(Wire, AXP192_SLAVE_ADDRESS) == AXP_PASS) {
        axp.setPowerOutPut(AXP192_LDO2, AXP202_ON);  // LoRa
        axp.setPowerOutPut(AXP192_LDO3, AXP202_ON);  // GPS
        Serial.println("[PWR] AXP192 initialized");
    }
#endif

    // Init System 5
    s5_init(&nodeState);
    nodeState.my_id = getNodeId();
    nodeState.my_battery_pct = 100; // TODO: read from ADC/AXP
    Serial.printf("Node ID: %08X\n", nodeState.my_id);

    // Init GPS
    gps_init();

    // Init LoRa
    if (!lora_init()) {
        Serial.println("FATAL: LoRa init failed!");
        while (1) { delay(1000); }
    }
    lora_start_receive();

#if HAS_DISPLAY
    display.begin();
    display.setFont(u8g2_font_6x10_tr);
    display.clearBuffer();
    display.drawStr(0, 30, "MeshRoute S5");
    display.drawStr(0, 45, BOARD_NAME);
    display.sendBuffer();
    delay(1000);
#endif

    digitalWrite(LED_PIN, LOW);
    Serial.println("Ready. Type 'help' for commands.\n");
}

// ── Loop ───────────────────────────────────────────────────────

void loop() {
    uint32_t now = millis();

    // GPS update
    gps_update();

    // Update position if GPS available
    position_t pos = gps_get_position();
    if (pos.valid && pos.source == POS_SOURCE_GPS) {
        s5_update_position(&nodeState, pos.lat, pos.lon);
    }

    // Receive packets
    if (lora_available()) {
        uint8_t len;
        int16_t rssi;
        int8_t snr;

        if (lora_receive(rxBuf, &len, &rssi, &snr)) {
            packetsRx++;
            digitalWrite(LED_PIN, HIGH);

            s5_wire_header_t hdr;
            const uint8_t *payload;

            if (s5_wire_unpack(rxBuf, len, &hdr, &payload)) {
                // Skip own packets and duplicates
                if (hdr.src != nodeState.my_id && !isDuplicate(hdr.packet_id)) {
                    switch (hdr.type) {
                        case PKT_TYPE_OGM:
                            handleOGM(&hdr, payload, rssi, snr);
                            break;
                        case PKT_TYPE_DATA:
                            handleData(&hdr, payload, rssi, snr);
                            break;
                    }
                }
            }

            digitalWrite(LED_PIN, LOW);
        }
    }

    // Send periodic OGM
    if (now - lastOGM >= OGM_INTERVAL_MS) {
        lastOGM = now;
        sendOGM();
    }

    // Periodic maintenance
    if (now - lastMaintenance >= MAINTENANCE_INTERVAL_MS) {
        lastMaintenance = now;
        s5_maintenance(&nodeState, now);
    }

    // Display update
#if HAS_DISPLAY
    if (now - lastDisplay >= DISPLAY_UPDATE_MS) {
        lastDisplay = now;
        updateDisplay();
    }
#endif

    // Serial commands
    handleSerial();
}
