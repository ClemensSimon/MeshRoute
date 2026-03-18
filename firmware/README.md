# MeshRoute System 5 — ESP32 Firmware

Standalone LoRa mesh routing firmware for testing System 5 geo-clustered multi-path routing on real hardware.

## Supported Boards

| Board | MCU | LoRa Chip | GPS | Display | Buy |
|-------|-----|-----------|-----|---------|-----|
| **Heltec WiFi LoRa 32 V3** | ESP32-S3 | SX1262 | No | OLED 0.96" | [Heltec Store](https://heltec.org/project/wifi-lora-32-v3/) |
| **TTGO T-Beam v1.1** | ESP32 | SX1276 | NEO-6M | OLED 0.96" | [LilyGO Store](https://www.lilygo.cc/products/t-beam-v1-1-esp32-lora-module) |
| **RAK WisBlock 4631** | nRF52840 | SX1262 | RAK1910 module | No | [RAK Store](https://store.rakwireless.com/products/rak4631-lpwan-node) |

> **Note:** T-Beam v1.2+ uses AXP2101 instead of AXP192. This firmware supports v1.1 only. For v1.2+, the power management code needs adaptation.

## Prerequisites

### 1. Install PlatformIO

**Option A: VS Code Extension (recommended)**
1. Install [Visual Studio Code](https://code.visualstudio.com/)
2. Open Extensions (Ctrl+Shift+X)
3. Search "PlatformIO IDE" and install
4. Restart VS Code

**Option B: CLI only**
```bash
pip install platformio
```

### 2. Install USB Drivers

| Board | Driver | Download |
|-------|--------|----------|
| Heltec V3 | CP2102 (USB-C) | Usually auto-detected |
| T-Beam v1.1 | CP2104 | [Silicon Labs](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers) |
| RAK4631 | nRF USB (J-Link) | Requires [Segger J-Link](https://www.segger.com/downloads/jlink/) or UF2 bootloader |

### 3. Connect Board via USB

Plug in the board. Check which port it appears on:

**Windows:**
```
Device Manager → Ports (COM & LPT) → Silicon Labs CP210x (COMx)
```

**Linux:**
```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

**macOS:**
```bash
ls /dev/cu.usbserial* /dev/cu.SLAB*
```

## Build & Flash

### Clone the Repository

```bash
git clone https://github.com/ClemensSimon/MeshRoute.git
cd MeshRoute/firmware
```

### Build for Your Board

```bash
# Heltec WiFi LoRa 32 V3
pio run -e heltec_v3

# TTGO T-Beam v1.1
pio run -e tbeam

# RAK WisBlock 4631
pio run -e rak4631
```

### Flash (Upload)

```bash
# Heltec V3 (auto-detects COM port)
pio run -e heltec_v3 -t upload

# T-Beam
pio run -e tbeam -t upload

# RAK4631 (requires J-Link or UF2 bootloader)
pio run -e rak4631 -t upload
```

### Open Serial Monitor

```bash
pio device monitor -b 115200
```

You should see:
```
=== MeshRoute System 5 v0.1.0 ===
Board: Heltec V3
[WDT] Watchdog enabled (30s)
Node ID: A1B2C3D4
[LoRa] Initializing Heltec V3... OK
[GPS] No GPS hardware — using triangulation/manual
Ready. Type 'help' for commands.
```

## First Steps

### 1. Set Position (boards without GPS)

The Heltec V3 has no GPS. Set your position manually:

```
pos 48.1351 11.5820
```
(This example is Munich. Use your actual coordinates.)

T-Beam and RAK4631 with GPS module get position automatically after a few minutes outdoors.

### 2. Wait for Neighbors

The node sends an OGM (Originator Message) every 30 seconds. Other nodes within LoRa range will appear:

```
[OGM] From A1B2C3D4: (48.1351,11.5820) batt=92% cluster=42 q=0.85 snr=7
```

Check your neighbor table:
```
status
```

### 3. Send a Test Message

```
send A1B2C3D4 Hello from my node!
```

Replace `A1B2C3D4` with the Node ID of your target (shown in the `status` output).

If System 5 has a route, it sends **directed** (1 TX):
```
[TX] Sent to A1B2C3D4 via DIRECT (42 bytes)
```

If no route exists, it falls back to **flooding**:
```
[TX] Sent to A1B2C3D4 via FLOOD (42 bytes)
```

### 4. Monitor Routing

Watch incoming packets and routing decisions:
```
[DATA] Routing A1B2C3D4->E5F6A7B8 via next_hop C9D0E1F2
[DATA] Received message from A1B2C3D4: Hello from my node!
[DATA] Fallback flood A1B2C3D4->E5F6A7B8 (hop 3)
```

## Serial Commands

| Command | Description | Example |
|---------|-------------|---------|
| `help` | Show all commands | `help` |
| `status` | Node status, neighbors, stats | `status` |
| `send <id> <msg>` | Send message to node (8-char hex ID) | `send A1B2C3D4 Hello!` |
| `pos <lat> <lon>` | Set manual GPS position | `pos 48.1351 11.5820` |

## Network Setup (2-3 Nodes)

### Minimal Test: 2 Nodes

1. Flash both boards
2. Set positions on both (if no GPS): `pos <lat> <lon>`
3. Wait 30-60 seconds for OGM exchange
4. Run `status` on both — they should see each other as neighbors
5. Send a message from one to the other

### Cross-Cluster Test: 3+ Nodes

For System 5 routing to show its advantage, you need nodes in **different clusters**:

1. Place Node A at location X (e.g. your home)
2. Place Node B 500m-2km away (e.g. a friend's house)
3. Place Node C between them or near Node B
4. Nodes A and B will be in different geohash clusters
5. Node C becomes a **border node** if it can reach both clusters
6. Messages from A to B will route through C via the **bridge link**

```
Cluster 0          Bridge          Cluster 1
[Node A] -------- [Node C] -------- [Node B]
  home              park             friend
```

### What to Observe

- **Direct routing**: Message goes A → C → B (2 TX total)
- **Flooding would**: A broadcasts to all neighbors, C rebroadcasts to all, etc. (many TX)
- **Check with `status`**: See which nodes are border nodes, which cluster each is in

## LED Indicators

| Board | LED Behavior |
|-------|-------------|
| Heltec V3 | Blink on packet RX |
| T-Beam | Blink on packet RX |
| RAK4631 | Green LED blink on packet RX |

## OLED Display (Heltec V3 / T-Beam)

The display shows 5 lines:
```
S5 A1B2C3D4    Heltec V3
C42 BRD NHS:0.8
Neighbors: 3
Pos: GPS
TX:12 RX:45 R:8
```

- **Line 1**: Node ID + board name
- **Line 2**: Cluster ID, border status (BRD), Network Health Score
- **Line 3**: Number of discovered neighbors
- **Line 4**: Position source (GPS / MANUAL / TRIANG / INHERIT / NONE)
- **Line 5**: Stats — packets transmitted, received, routed

## Boards Without GPS

The Heltec V3 has no GPS module. Three fallback methods are available:

### 1. Manual Position (recommended for fixed installations)
```
pos 48.1351 11.5820
```
Set once — the node remembers until reboot.

### 2. RSSI Triangulation
If 3+ neighbors with known GPS positions are in range, the node estimates its own position from signal strengths. This happens automatically — no user action needed.

Accuracy: ~500m (enough for cluster assignment).

### 3. Cluster Inheritance
If fewer than 3 GPS-equipped neighbors are available, the node copies the cluster ID from its strongest neighbor. No position is computed, but the node joins the correct cluster.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `FATAL: LoRa init failed!` | Check SPI connections. Board auto-reboots after 5s. |
| No neighbors after 2 minutes | Ensure both nodes are on the same frequency (EU868). Check antenna. |
| `status` shows 0 neighbors | Other node may be out of range. Try closer (<1km in urban, <5km rural). |
| Position shows "NONE" | Set manually with `pos` command, or wait for GPS fix (outdoors, 1-5 min). |
| T-Beam screen blank | OLED may need different I2C address. Check solder jumper on PCB. |
| RAK4631 won't flash | Use UF2 bootloader: double-tap reset, drag .uf2 file to USB drive. |
| Watchdog reboot loop | LoRa radio not responding. Check antenna connector. |

## LoRa Parameters

| Parameter | Value | Note |
|-----------|-------|------|
| Frequency | 868.0 MHz | EU868 band |
| Bandwidth | 125 kHz | Standard LoRa |
| Spreading Factor | SF7 | Fastest, ~2km urban range |
| Coding Rate | 4/5 | Standard |
| TX Power | 14 dBm | EU limit |
| Sync Word | 0x12 | Private network (not Meshtastic) |
| Preamble | 8 symbols | Standard |

> **Important:** This firmware uses sync word `0x12` (private). Meshtastic uses `0x2B`. The two networks do **not** interfere with each other but also cannot communicate directly. This is intentional for testing — a production version would use the Meshtastic sync word.

## Project Structure

```
firmware/
├── platformio.ini              Build config (3 boards + native tests)
├── include/
│   ├── board_config.h          Pin definitions per board
│   ├── system5.h               Routing core API
│   ├── lora_hal.h              LoRa hardware abstraction
│   ├── gps_hal.h               GPS + triangulation + cluster inheritance
│   └── wire_protocol.h         Over-the-air packet format (22-byte header)
├── src/
│   ├── main.cpp                Application (OGM, routing, display, serial CLI)
│   ├── system5.c               Routing logic (geohash, multi-path, QoS, fallback)
│   ├── lora_hal.cpp            RadioLib wrapper (SX1276 + SX1262)
│   ├── gps_hal.cpp             GPS with 3 fallback levels
│   └── wire_protocol.c         Packet serialize/deserialize
└── test/
    └── test_system5.c          Unit tests (run on PC)
```

## License

MIT — see [LICENSE](../LICENSE)
