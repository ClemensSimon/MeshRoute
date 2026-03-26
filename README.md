# WalkFlood

**Zero-overhead mesh routing for LoRa — 88% delivery at 1200 nodes where managed flooding collapses to 4%.**

**[Live Demo](https://clemenssimon.github.io/MeshRoute/)** | **[Live Simulator](https://clemenssimon.github.io/MeshRoute/simulator.html)** | **[RFC](docs/rfc-walkflood.md)** | **[How It Works](https://clemenssimon.github.io/MeshRoute/how-it-works.html)**

## The Protocol: 4 Phases

```
1. LEARN     Listen to traffic. Build routing table passively. Zero overhead.
2. DIRECT    Route known → forward hop-by-hop. 1 TX per hop.
3. WALK      Stuck → step toward the neighbor most likely to know a route.
4. MINI-FLOOD Still stuck → tiny scoped flood (2 neighbors, 4 hops, ~30 TX).
```

No GPS. No beacons. No control packets. 12 bytes per route entry. ~3KB RAM for 235 nodes.

## Results

Tested on identical networks with half-duplex radio, collisions, and asymmetric links:

| Scenario | Managed Flood | **WalkFlood** | Improvement |
|----------|:---:|:---:|---|
| Small (20 nodes, 1km) | 87% / 1,640 TX | **100% / 159 TX** | 10x less TX |
| Medium (100 nodes, 5km) | 27% / 3,380 TX | **100% / 368 TX** | 3.7x delivery, 9x less TX |
| Node Kill 20% | 16% / 2,696 TX | **100% / 383 TX** | 6.3x delivery, 7x less TX |
| Stress 30% degraded | 19% / 3,380 TX | **99% / 492 TX** | 5.2x delivery, 7x less TX |
| Bay Area (235 nodes) | 6% / 6,752 TX | **84% / 8,894 TX** | 14x delivery |
| **Bay Area (1200 nodes)** | **4% / 38,644 TX** | **88% / 5,909 TX** | **22x delivery, 6.5x less TX** |

WalkFlood scales *better* with more nodes (88% at 1200 vs 84% at 235) because denser networks provide more passive learning opportunities.

## Why Flooding Fails

A mountain node with 234 neighbors broadcasting blocks ALL 234 nodes via half-duplex for ~2.3 seconds (SF12). One TX kills the entire network. This is the half-duplex cascade — the root cause of managed flooding's collapse at scale.

WalkFlood avoids this: directed routing means each TX blocks only 1 neighbor, not 234.

## How It Learns Routes

1. **NodeInfo** (already sent by Meshtastic): discover direct neighbors
2. **Overhearing**: hear B forward a packet from C → learn "C reachable via B, 2 hops"
3. **Dijkstra bootstrap** (weight = -log(quality)): find most *reliable* paths, not shortest
4. **Delivery feedback**: each successful delivery teaches the full path to all nodes along it

Mathematically proven: a 5-hop path through hills [0.7, 0.6, 0.5, 0.6, 0.7] has 99.5% delivery at 8.2 TX, while a 2-hop path through a mountain [0.05, 0.05] has only 11.3% delivery at 79.4 TX. Dijkstra finds the hill path automatically.

## Comparison with Other Protocols

| Protocol | Control Overhead | Memory | GPS? | Delivery (Bay Area 1200n) |
|----------|:---:|:---:|:---:|:---:|
| Managed Flooding (Meshtastic) | O(n) per hop | NodeDB | No | 4% |
| BATMAN IV/V | O(n²) OGMs/sec | 10-14 KB | No | — |
| AODV | Flood per new dest | 20-30 B/route | No | — |
| OLSR | O(n) TC messages | Full table | No | — |
| RPL | Trickle DIO beacons | 1 parent | No | — |
| MeshCore | Flood-then-direct | Unknown | No | — |
| **WalkFlood** | **Zero** | **12 B/route** | **No** | **88%** |

## Development History

1. **System 5** — First attempt: geo-clustering, OGM beacons, multi-path routing. Community feedback: *"too complex"* (h3lix1, Meshtastic core dev). Scrapped.
2. **EchoRoute** — Pure directed routing, zero flooding. Small mesh: 100%. Bay Area: 21%. Not enough.
3. **Research** — 11 parallel agents analyzed BATMAN, AODV, OLSR, RPL, goTenna ECHO, MeshCore, fountain codes, LoRa physics, BayMesh MQTT data, real Meshtastic firmware code, and probability theory.
4. **WalkFlood** — Walk + Mini-Flood fallback. Bay Area 1200n: 88%. Beats all previous approaches.

## Live Simulator

The simulator runs Managed Flooding vs WalkFlood side-by-side on identical networks:

- **Step hop-by-hop** through both algorithms simultaneously
- **17 scenarios** from small local mesh to 1200-node Bay Area
- **Click nodes** to choose source and destination
- **See the difference**: flooding lights up the entire network; WalkFlood follows a directed path

## Python Simulator

```bash
cd simulator
python run.py                      # run all scenarios
python run.py --scenario 1         # single scenario
python run.py --parallel scenarios # parallel across CPU cores
```

### Routers Available

| Key | Router | Description |
|-----|--------|-------------|
| `managed_7hop` | Managed Flood | Meshtastic-style with SNR suppression |
| `system5` | System 5 | Geo-clustered multi-path (legacy) |
| `echoroute` | EchoRoute | Pure directed, no flooding |
| `walkflood` | **WalkFlood** | Directed + Walk + Mini-Flood |

### Simulator Architecture

```
simulator/
  run.py        — CLI entry point
  meshsim.py    — Network simulation (nodes, links, clusters, mobility)
  routing.py    — All routers (ManagedFlood, System5, EchoRoute, WalkFlood)
  lora_model.py — EU 868MHz LoRa model (terrain, duty cycle, collisions, half-duplex)
  benchmark.py  — Scenarios, parallelized benchmarking
  geohash.py    — Geographic clustering
```

## Validation Plan

1. **Meshtasticator** — Official Meshtastic simulator running real firmware
2. **BayMesh MQTT** — Real traffic data from `mqtt.bayme.sh` (public)
3. **Malla** — Packet analyzer for topology + SNR + delivery rate extraction

## ESP32 Firmware Prototype

Working standalone firmware for three LoRa boards (System 5 version — WalkFlood firmware update planned):

```bash
pio run -e heltec_v3   # Heltec WiFi LoRa 32 V3
pio run -e tbeam       # TTGO T-Beam v1.1
pio run -e rak4631     # RAK WisBlock 4631
```

## Honest Limitations

- **Cold start**: New nodes need ~minutes of traffic to learn routes
- **Broadcast**: WalkFlood is for unicast. Broadcasts still need managed flooding
- **Simulation only**: Not yet validated against real Meshtastic firmware or live meshes
- **Propagation model**: Uses simplified log-distance + Okumura-Hata, not calibrated to real LoRa data

## Project Status

- [x] WalkFlood algorithm design and simulation
- [x] Interactive website with development timeline
- [x] Python simulator (7 routers, 17+ scenarios, EU868 LoRa model)
- [x] Live simulator with hop-by-hop visualization
- [x] RFC specification (docs/rfc-walkflood.md)
- [x] Bay Area 1200-node testing
- [x] Mathematical analysis (probability theory, optimal retries)
- [x] Research: 11 agents analyzed BATMAN/AODV/OLSR/RPL/goTenna/MeshCore
- [ ] Validation against Meshtasticator (real firmware)
- [ ] Real-world data from BayMesh MQTT
- [ ] WalkFlood ESP32 firmware
- [ ] Field testing with LoRa hardware

## License

MIT — see [LICENSE](LICENSE)

## Author

[Clemens Simon](https://github.com/ClemensSimon)
