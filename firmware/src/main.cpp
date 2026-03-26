/**
 * MeshRoute WalkFlood — Main Application
 *
 * Standalone LoRa mesh node with WalkFlood routing.
 * Supports: Heltec V3, T-Beam, RAK4631
 *
 * Routing strategy:
 * 1. On send: check route table → directed (walk) if known, else flood
 * 2. On receive: learn routes from overheard traffic
 * 3. If directed send fails: try 2 best-scoring neighbors (mini-flood)
 * 4. Broadcasts use MPR to reduce redundant rebroadcasts
 */

#include <Arduino.h>
#if defined(ESP32) || defined(ESP_PLATFORM)
#include <esp_task_wdt.h>
#define WDT_TIMEOUT_S 30
#endif
#include "board_config.h"
#include "walkflood.h"
#include "system5.h"      // still needed for s5_node_state_t (OGM, position, etc.)
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

static s5_node_state_t nodeState;     // kept for OGM/GPS/wire_protocol compat
static wf_state_t wfState;            // WalkFlood routing state
static uint8_t txBuf[LORA_MAX_PACKET_SIZE];
static uint8_t rxBuf[LORA_MAX_PACKET_SIZE];
static uint32_t lastOGM = 0;
static uint32_t lastMaintenance = 0;
static uint32_t lastDisplay = 0;
static uint32_t packetsRx = 0;
static uint32_t packetsTx = 0;
static uint32_t packetsRouted = 0;

// Dedup ring buffer (last 128 packet IDs)
#define DEDUP_SIZE 128
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
    float quality = ((float)rssi + 120.0f) / 70.0f;
    if (quality > 1.0f) quality = 1.0f;
    if (quality < 0.0f) quality = 0.0f;

    // Update System 5 neighbor table (for position/cluster compat)
    s5_update_neighbor(&nodeState, hdr->src, ogm->lat, ogm->lon,
                        ogm->battery_pct, snr, quality);

    // Update neighbor's last_heard
    for (uint8_t i = 0; i < nodeState.neighbor_count; i++) {
        if (nodeState.neighbors[i].id == hdr->src) {
            nodeState.neighbors[i].last_heard_ms = millis();
            break;
        }
    }

    // WalkFlood: learn neighbor with degree info
    wf_learn_neighbor(&wfState, hdr->src, quality, ogm->neighbor_count);

    // Update neighbor last_seen for expiry
    for (uint8_t i = 0; i < wfState.neighbor_count; i++) {
        if (wfState.neighbors[i].node_id == hdr->src) {
            wfState.neighbors[i].last_seen_ms = millis();
            break;
        }
    }

    // Learn route from OGM source (1-hop path)
    uint32_t path[1] = { hdr->src };
    wf_learn_from_packet(&wfState, path, 1, quality);

    // Refresh route timestamp
    for (uint16_t i = 0; i < wfState.route_count; i++) {
        if (wfState.routes[i].dest_id == hdr->src) {
            wfState.routes[i].last_seen = (uint16_t)(millis() / 1000);
            break;
        }
    }

    // Recompute MPR set after neighbor change
    wf_compute_mpr_set(&wfState);

    Serial.printf("[OGM] From %08X: (%.4f,%.4f) batt=%u%% q=%.2f snr=%d deg=%u\n",
                  hdr->src, ogm->lat, ogm->lon, ogm->battery_pct,
                  quality, snr, ogm->neighbor_count);
}

// ── Handle received data packet ────────────────────────────────

static void handleData(const s5_wire_header_t *hdr, const uint8_t *payload,
                        int16_t rssi, int8_t snr) {
    // Learn routes from this packet
    // We know: hdr->src originated this, and it reached us (possibly via relays)
    // The hop_count tells us how many hops it took
    // For route learning, we build a minimal path: [src]
    // (We don't have full path in the header, just src + hop_count)
    float quality = ((float)rssi + 120.0f) / 70.0f;
    if (quality > 1.0f) quality = 1.0f;
    if (quality < 0.0f) quality = 0.0f;

    uint32_t path[1] = { hdr->src };
    wf_learn_from_packet(&wfState, path, 1, quality);

    // Refresh route timestamps
    for (uint16_t i = 0; i < wfState.route_count; i++) {
        if (wfState.routes[i].dest_id == hdr->src) {
            wfState.routes[i].last_seen = (uint16_t)(millis() / 1000);
            break;
        }
    }

    // Is this packet for us?
    if (hdr->dst == nodeState.my_id) {
        Serial.printf("[DATA] Received message from %08X: %.*s\n",
                      hdr->src, hdr->payload_len, payload);
        return;
    }

    // TTL check
    if (hdr->ttl <= 1 || hdr->hop_count >= 20) {
        Serial.printf("[DATA] Dropped %08X->%08X (TTL/hops)\n",
                      hdr->src, hdr->dst);
        return;
    }

    // --- WalkFlood routing decision ---

    // Is this a directed packet for us to forward?
    if (hdr->type == PKT_TYPE_WALKFLOOD && hdr->next_hop != 0 &&
        hdr->next_hop != nodeState.my_id) {
        // Not for us to relay
        return;
    }

    // Try walk: do we know a route?
    uint32_t next = wf_get_next_hop(&wfState, hdr->dst);

    if (next != 0) {
        // WALK: directed forward
        Serial.printf("[WF] Walk %08X->%08X via %08X\n",
                      hdr->src, hdr->dst, next);
        s5_wire_header_t fwd = *hdr;
        fwd.type = PKT_TYPE_WALKFLOOD;
        fwd.hop_count++;
        fwd.ttl--;
        fwd.next_hop = next;
        uint8_t len = s5_wire_pack(&fwd, payload, txBuf, sizeof(txBuf));
        if (len > 0 && lora_send(txBuf, len)) {
            packetsTx++;
            packetsRouted++;
            wfState.walks++;
        } else {
            // Walk failed — try mini-flood
            goto mini_flood;
        }
        return;
    }

    // No route — try mini-flood with 2 best neighbors
mini_flood:
    {
        wf_walk_score_t best[2];
        uint8_t n_best = wf_get_best_walkers(&wfState, hdr->dst, best, 2);

        if (n_best > 0) {
            Serial.printf("[WF] Mini-flood %08X->%08X to %u neighbors\n",
                          hdr->src, hdr->dst, n_best);
            for (uint8_t i = 0; i < n_best; i++) {
                s5_wire_header_t fwd = *hdr;
                fwd.type = PKT_TYPE_WALKFLOOD;
                fwd.hop_count++;
                fwd.ttl--;
                fwd.next_hop = best[i].neighbor_id;
                uint8_t len = s5_wire_pack(&fwd, payload, txBuf, sizeof(txBuf));
                if (len > 0 && lora_send(txBuf, len)) {
                    packetsTx++;
                    packetsRouted++;
                }
            }
            wfState.mini_floods++;
            return;
        }

        // Full flood fallback
        Serial.printf("[WF] Flood %08X->%08X (hop %u)\n",
                      hdr->src, hdr->dst, hdr->hop_count);

        // MPR check: should we relay this broadcast?
        // For data packets being flooded, check if we are useful as relay
        if (hdr->hop_count > 1 && !wf_is_mpr(&wfState, hdr->src)) {
            // We are not an MPR for this sender — skip relay to reduce floods
            // But only apply MPR filtering after first hop (give packets a chance)
            Serial.printf("[WF] Not MPR for %08X, skip relay\n", hdr->src);
            return;
        }

        s5_wire_header_t fwd = *hdr;
        fwd.hop_count++;
        fwd.ttl--;
        fwd.next_hop = 0; // flood = no directed next hop
        uint8_t len = s5_wire_pack(&fwd, payload, txBuf, sizeof(txBuf));
        if (len > 0 && lora_send(txBuf, len)) {
            packetsTx++;
            packetsRouted++;
            wfState.floods++;
        }
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
        Serial.printf("[OGM] Sent (%u bytes, %u neighbors, %u routes)\n",
                      len, wfState.neighbor_count, wfState.route_count);
    }
}

// ── Serial Command Handler ─────────────────────────────────────

static void handleSerial(void) {
    if (!Serial.available()) return;

    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line.startsWith("send ")) {
        // "send <dst_hex> <message>"
        uint32_t dst = strtoul(line.substring(5, 13).c_str(), NULL, 16);
        String msg = line.substring(14);

        // WalkFlood routing decision
        uint32_t next = wf_get_next_hop(&wfState, dst);

        if (next != 0) {
            // Walk: directed send
            uint8_t len = s5_create_data(&nodeState, dst, next, 3,
                                          (const uint8_t *)msg.c_str(), msg.length(),
                                          txBuf, sizeof(txBuf));
            // Override packet type to WALKFLOOD
            if (len > 0) {
                txBuf[1] = PKT_TYPE_WALKFLOOD; // type field offset in header
            }
            if (len > 0 && lora_send(txBuf, len)) {
                packetsTx++;
                wfState.walks++;
                Serial.printf("[TX] Sent to %08X via WALK (next=%08X, %u bytes)\n",
                              dst, next, len);
            } else {
                // Walk failed — try mini-flood
                wf_walk_score_t best[2];
                uint8_t n_best = wf_get_best_walkers(&wfState, dst, best, 2);
                bool sent = false;
                for (uint8_t i = 0; i < n_best; i++) {
                    uint8_t len2 = s5_create_data(&nodeState, dst, best[i].neighbor_id, 3,
                                                    (const uint8_t *)msg.c_str(), msg.length(),
                                                    txBuf, sizeof(txBuf));
                    if (len2 > 0) txBuf[1] = PKT_TYPE_WALKFLOOD;
                    if (len2 > 0 && lora_send(txBuf, len2)) {
                        packetsTx++;
                        sent = true;
                    }
                }
                if (sent) {
                    wfState.mini_floods++;
                    Serial.printf("[TX] Sent to %08X via MINI-FLOOD (%u neighbors)\n",
                                  dst, n_best);
                } else {
                    Serial.println("[TX] FAILED");
                }
            }
        } else {
            // Flood: no route known
            uint8_t len = s5_create_data(&nodeState, dst, 0, 3,
                                          (const uint8_t *)msg.c_str(), msg.length(),
                                          txBuf, sizeof(txBuf));
            if (len > 0 && lora_send(txBuf, len)) {
                packetsTx++;
                wfState.floods++;
                Serial.printf("[TX] Sent to %08X via FLOOD (%u bytes)\n", dst, len);
            } else {
                Serial.println("[TX] FAILED");
            }
        }

    } else if (line == "status") {
        Serial.printf("\n=== WalkFlood Node %08X (%s) ===\n", nodeState.my_id, BOARD_NAME);
        position_t pos = gps_get_position();
        Serial.printf("Position: %.6f, %.6f (source: %u)\n", pos.lat, pos.lon, pos.source);
        Serial.printf("Neighbors: %u  Routes: %u\n",
                      wfState.neighbor_count, wfState.route_count);
        Serial.printf("MPR set: %u nodes\n", wfState.mpr_count);

        // Neighbors
        Serial.println("--- Neighbors ---");
        for (uint8_t i = 0; i < wfState.neighbor_count; i++) {
            const wf_neighbor_t *n = &wfState.neighbors[i];
            Serial.printf("  %08X: q=%.2f deg=%u %s\n",
                          n->node_id, n->quality, n->degree,
                          wf_is_mpr(&wfState, n->node_id) ? "[MPR]" : "");
        }

        // Route table (first 20)
        Serial.println("--- Routes (top 20) ---");
        uint16_t show = wfState.route_count;
        if (show > 20) show = 20;
        for (uint16_t i = 0; i < show; i++) {
            const wf_route_entry_t *r = &wfState.routes[i];
            Serial.printf("  %08X -> via %08X (%u hops, q=%u)\n",
                          r->dest_id, r->next_hop, r->hop_count, r->quality);
        }

        Serial.printf("Stats: TX=%u RX=%u Routed=%u Walk=%u Flood=%u MiniFlood=%u\n\n",
                      packetsTx, packetsRx, packetsRouted,
                      wfState.walks, wfState.floods, wfState.mini_floods);

    } else if (line.startsWith("pos ")) {
        // "pos <lat> <lon>" — set manual position
        float lat = line.substring(4).toFloat();
        int space = line.indexOf(' ', 5);
        float lon = line.substring(space + 1).toFloat();
        gps_set_manual(lat, lon);
        s5_update_position(&nodeState, lat, lon);

    } else if (line == "help") {
        Serial.println("\nCommands:");
        Serial.println("  send <dst_hex> <message>  - Send message to node");
        Serial.println("  status                    - Show node status + routes");
        Serial.println("  pos <lat> <lon>           - Set manual position");
        Serial.println("  help                      - This help\n");
    }
}

// ── Display ────────────────────────────────────────────────────

#if HAS_DISPLAY
static void updateDisplay(void) {
    display.clearBuffer();
    display.setFont(u8g2_font_6x10_tr);

    // Line 1: Node ID
    char line[32];
    snprintf(line, sizeof(line), "WF %08X", nodeState.my_id);
    display.drawStr(0, 10, line);
    display.drawStr(90, 10, BOARD_NAME);

    // Line 2: Neighbors + Routes
    snprintf(line, sizeof(line), "N:%u R:%u MPR:%u",
             wfState.neighbor_count, wfState.route_count, wfState.mpr_count);
    display.drawStr(0, 22, line);

    // Line 3: Walk/Flood stats
    snprintf(line, sizeof(line), "W:%u F:%u MF:%u",
             wfState.walks, wfState.floods, wfState.mini_floods);
    display.drawStr(0, 34, line);

    // Line 4: Position source
    position_t pos = gps_get_position();
    const char *src_str[] = {"NONE", "GPS", "MANUAL", "TRIANG", "INHERIT"};
    const char *ps = (pos.valid && pos.source < 5) ? src_str[pos.source] : "???";
    snprintf(line, sizeof(line), "Pos: %s", ps);
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
    Serial.printf("\n\n=== MeshRoute WalkFlood v%s ===\n", S5_FIRMWARE_VERSION);
    Serial.printf("Board: %s\n", BOARD_NAME);

    // Watchdog (30s timeout, reboot on hang)
#if defined(ESP32) || defined(ESP_PLATFORM)
    esp_task_wdt_init(WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
    Serial.println("[WDT] Watchdog enabled (30s)");
#endif

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

    // Init System 5 state (for OGM/wire_protocol compat)
    s5_init(&nodeState);
    nodeState.my_id = getNodeId();
    nodeState.my_battery_pct = 100;
    s5_wire_seed_packet_id(nodeState.my_id, millis());

    // Init WalkFlood
    wf_init(&wfState);
    wfState.my_id = nodeState.my_id;
    Serial.printf("Node ID: %08X\n", nodeState.my_id);

    // Init GPS
    gps_init();

    // Init LoRa
    if (!lora_init()) {
        Serial.println("FATAL: LoRa init failed! Rebooting in 5s...");
        delay(5000);
#if defined(ESP32) || defined(ESP_PLATFORM)
        ESP.restart();
#else
        NVIC_SystemReset();
#endif
    }
    lora_start_receive();

#if HAS_DISPLAY
    display.begin();
    display.setFont(u8g2_font_6x10_tr);
    display.clearBuffer();
    display.drawStr(0, 30, "MeshRoute WF");
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
                        case PKT_TYPE_WALKFLOOD:
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

    // Periodic maintenance: expire routes + neighbors
    if (now - lastMaintenance >= MAINTENANCE_INTERVAL_MS) {
        lastMaintenance = now;
        wf_expire_routes(&wfState, now);
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

    // Feed watchdog
#if defined(ESP32) || defined(ESP_PLATFORM)
    esp_task_wdt_reset();
#endif
}
