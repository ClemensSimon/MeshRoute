/**
 * MeshRoute System 5 — LoRa HAL Implementation
 *
 * Uses RadioLib for cross-chip support (SX1276 + SX1262).
 */

#include "lora_hal.h"
#include "board_config.h"

#include <RadioLib.h>
#include <SPI.h>

// ── Radio Instance ─────────────────────────────────────────────

#if defined(LORA_CHIP_SX1262)
  static SPIClass loraSPI(HSPI);
  static SX1262 radio = new Module(LORA_CS, LORA_DIO1, LORA_RST, LORA_BUSY, loraSPI);
#elif defined(LORA_CHIP_SX1276)
  static SX1276 radio = new Module(LORA_CS, LORA_DIO0, LORA_RST, LORA_DIO1);
#endif

static volatile bool rxFlag = false;
static volatile bool txDone = false;

// ── ISR ────────────────────────────────────────────────────────

#if defined(ESP32) || defined(ESP_PLATFORM)
  static void IRAM_ATTR onReceive(void) { rxFlag = true; }
  static void IRAM_ATTR onTransmitDone(void) { txDone = true; }
#else
  static void onReceive(void) { rxFlag = true; }
  static void onTransmitDone(void) { txDone = true; }
#endif

// ── Init ───────────────────────────────────────────────────────

bool lora_init(void) {
#if defined(LORA_CHIP_SX1262)
    loraSPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
#endif

    Serial.print("[LoRa] Initializing " BOARD_NAME "... ");

    int state = radio.begin(
        LORA_FREQUENCY,
        LORA_BANDWIDTH,
        LORA_SPREADING,
        LORA_CODING_RATE,
        LORA_SYNC_WORD,
        LORA_TX_POWER,
        LORA_PREAMBLE
    );

    if (state != RADIOLIB_ERR_NONE) {
        Serial.print("FAIL! Error: ");
        Serial.println(state);
        return false;
    }

#if defined(LORA_CHIP_SX1262)
    // SX1262 specific: set DIO2 as RF switch control
    radio.setDio2AsRfSwitch(true);
    // Set RX gain to boosted
    radio.setRxBoostedGainMode(true);
#endif

    // Set CRC on
    radio.setCRC(true);

    // Set interrupt callbacks
    radio.setDio1Action(onReceive);

    Serial.println("OK");
    return true;
}

// ── Send ───────────────────────────────────────────────────────

bool lora_send(const uint8_t *data, uint8_t len) {
    if (len > LORA_MAX_PACKET_SIZE) return false;

    txDone = false;
    int state = radio.startTransmit(const_cast<uint8_t*>(data), len);
    if (state != RADIOLIB_ERR_NONE) return false;

    // Wait for TX complete (with timeout)
    uint32_t start = millis();
    while (!txDone && (millis() - start) < 5000) {
        yield();
    }

    // Return to RX mode
    lora_start_receive();
    return txDone;
}

// ── Receive ────────────────────────────────────────────────────

bool lora_available(void) {
    return rxFlag;
}

bool lora_receive(uint8_t *buf, uint8_t *len, int16_t *rssi, int8_t *snr) {
    if (!rxFlag) return false;
    rxFlag = false;

    int numBytes = radio.getPacketLength();
    if (numBytes <= 0 || numBytes > LORA_MAX_PACKET_SIZE) {
        lora_start_receive();
        return false;
    }

    int state = radio.readData(buf, numBytes);
    *len = (uint8_t)numBytes;
    *rssi = (int16_t)radio.getRSSI();
    *snr = (int8_t)radio.getSNR();

    // Restart listening
    lora_start_receive();

    return (state == RADIOLIB_ERR_NONE);
}

void lora_start_receive(void) {
    rxFlag = false;
    radio.startReceive();
}

// ── Time on Air ────────────────────────────────────────────────

uint32_t lora_time_on_air_ms(uint8_t len) {
    return (uint32_t)radio.getTimeOnAir(len) / 1000;
}
