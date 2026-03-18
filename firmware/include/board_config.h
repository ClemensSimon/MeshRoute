/**
 * MeshRoute System 5 — Board Configuration
 *
 * Pin definitions and hardware setup for supported boards.
 * Select board via build flag: -D BOARD_HELTEC_V3 / BOARD_TBEAM / BOARD_RAK4631
 */

#pragma once

// ── Heltec WiFi LoRa 32 V3 (ESP32-S3 + SX1262) ───────────────

#if defined(BOARD_HELTEC_V3)

#define BOARD_NAME          "Heltec V3"
#define HAS_DISPLAY         1
#define HAS_GPS             0
#define LORA_CHIP_SX1262    1

// SX1262 SPI pins
#define LORA_SCK            9
#define LORA_MISO           11
#define LORA_MOSI           10
#define LORA_CS             8
#define LORA_RST            12
#define LORA_DIO1           14
#define LORA_BUSY           13

// OLED Display (SSD1306 0.96")
#define OLED_SDA            17
#define OLED_SCL            18
#define OLED_RST            21

// LED
#define LED_PIN             35

// GPS (not available on Heltec V3)
#define GPS_RX              -1
#define GPS_TX              -1

// ── TTGO T-Beam (ESP32 + SX1276 + NEO-6M GPS) ────────────────

#elif defined(BOARD_TBEAM)

#define BOARD_NAME          "T-Beam"
#define HAS_DISPLAY         1
#define HAS_GPS             1
#define LORA_CHIP_SX1276    1

// SX1276 SPI pins
#define LORA_SCK            5
#define LORA_MISO           19
#define LORA_MOSI           27
#define LORA_CS             18
#define LORA_RST            23
#define LORA_DIO0           26
#define LORA_DIO1           33

// OLED Display (SSD1306 0.96")
#define OLED_SDA            21
#define OLED_SCL            22
#define OLED_RST            -1

// LED
#define LED_PIN             4

// GPS (NEO-6M on Serial1)
#define GPS_RX              34
#define GPS_TX              12
#define GPS_BAUD            9600

// AXP192 Power Management
#define HAS_AXP192          1

// ── RAK4631 (nRF52840 + SX1262) ───────────────────────────────

#elif defined(BOARD_RAK4631)

#define BOARD_NAME          "RAK4631"
#define HAS_DISPLAY         0
#define HAS_GPS             1
#define LORA_CHIP_SX1262    1

// SX1262 SPI pins (RAK4631 WisBlock)
#define LORA_SCK            43
#define LORA_MISO           45
#define LORA_MOSI           44
#define LORA_CS             42
#define LORA_RST            38
#define LORA_DIO1           47
#define LORA_BUSY           46

// No OLED on base RAK4631
#define OLED_SDA            -1
#define OLED_SCL            -1
#define OLED_RST            -1

// LED
#define LED_PIN             35

// GPS (RAK1910 GPS module on UART1)
#define GPS_RX              15
#define GPS_TX              16
#define GPS_BAUD            9600

#else
#error "No board defined! Use -D BOARD_HELTEC_V3, -D BOARD_TBEAM, or -D BOARD_RAK4631"
#endif

// ── Common LoRa Parameters (EU868) ────────────────────────────

#define LORA_FREQUENCY      868.0   // MHz
#define LORA_BANDWIDTH      125.0   // kHz
#define LORA_SPREADING      7       // SF7
#define LORA_CODING_RATE    5       // 4/5
#define LORA_TX_POWER       14      // dBm
#define LORA_PREAMBLE       8
#define LORA_SYNC_WORD      0x12    // private network

// ── Timing ────────────────────────────────────────────────────

#define OGM_INTERVAL_MS     30000   // send OGM every 30s
#define MAINTENANCE_INTERVAL_MS 30000
#define NEIGHBOR_TIMEOUT_MS 300000  // 5 min
#define DISPLAY_UPDATE_MS   1000
