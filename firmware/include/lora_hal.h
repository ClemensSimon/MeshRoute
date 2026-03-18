/**
 * MeshRoute System 5 — LoRa Hardware Abstraction Layer
 *
 * Wraps RadioLib for SX1276/SX1262 with a common API.
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>

#define LORA_MAX_PACKET_SIZE 256

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize LoRa radio hardware.
 * @return true on success
 */
bool lora_init(void);

/**
 * Send a packet over LoRa.
 * Blocks until transmission complete.
 * @param data    Packet data
 * @param len     Length in bytes (max LORA_MAX_PACKET_SIZE)
 * @return true on success
 */
bool lora_send(const uint8_t *data, uint8_t len);

/**
 * Check if a packet has been received.
 * Non-blocking.
 * @return true if a packet is available
 */
bool lora_available(void);

/**
 * Read a received packet.
 * @param buf     Buffer to store packet (min LORA_MAX_PACKET_SIZE)
 * @param len     Output: actual length received
 * @param rssi    Output: RSSI in dBm
 * @param snr     Output: SNR in dB
 * @return true on success
 */
bool lora_receive(uint8_t *buf, uint8_t *len, int16_t *rssi, int8_t *snr);

/**
 * Put radio in receive mode (continuous listen).
 */
void lora_start_receive(void);

/**
 * Get time-on-air for a packet of given size.
 * @return Time in milliseconds
 */
uint32_t lora_time_on_air_ms(uint8_t len);

#ifdef __cplusplus
}
#endif
