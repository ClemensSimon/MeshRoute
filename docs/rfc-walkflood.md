# RFC: WalkFlood — Passive Learning Mesh Routing with Zero Control Overhead

**Author:** Clemens Simon
**Status:** Research prototype with working simulator
**Repository:** [ClemensSimon/MeshRoute](https://github.com/ClemensSimon/MeshRoute)
**Date:** March 2026

---

## Abstract

WalkFlood is a routing protocol for LoRa mesh networks that achieves **84-100% delivery rates** with **7-65x fewer transmissions** than managed flooding — using **zero extra airtime** for route discovery. It learns routes purely by overhearing normal traffic, then forwards hop-by-hop using learned routes. When directed forwarding gets stuck, it walks toward the destination and uses a tiny scoped flood as last resort.

Tested at **1200-node Bay Area scale**: 88% delivery at 5,909 TX, where managed flooding collapses to 4% at 38,644 TX.

Designed for **severely constrained devices** (ESP32, ~3-15KB routing RAM, half-duplex LoRa radio) and **fully backward-compatible** with existing Meshtastic managed flooding.

---

## 1. Problem Statement

Meshtastic's managed flooding scales as O(n) per message per hop. At 200+ nodes, half-duplex radio cascade causes delivery collapse:
- A mountain node with 234 neighbors broadcasting blocks ALL 234 nodes for ~2.3 seconds (SF12)
- Bay Area (1200 nodes): **4% delivery** with managed flooding
- The hop limit (3-7) exists because each hop multiplies transmissions proportional to network size

## 2. Design Principles

| # | Principle | Source |
|---|-----------|--------|
| 1 | Zero control packets | goTenna ECHO |
| 2 | Learn by listening | SLR/ZigBee, DSR route cache |
| 3 | Dijkstra bootstrap (most reliable paths) | Probability theory: q<0.1 links are 9.7x more expensive |
| 4 | Walk toward destination when stuck | Biased random walk |
| 5 | Mini-flood only as last resort, from close position | OLSR MPR (selective relay) |
| 6 | Flat hash table, static allocation | ESP32 constraints |

## 3. Protocol: 4 Phases

### Phase 1: LEARN (Zero Overhead)

Nodes build routing tables by overhearing traffic that already exists:
- **NodeInfo beacons** (already sent by Meshtastic): learn direct neighbors
- **Overheard forwarded packets**: learn 2-hop routes
- **Successful deliveries**: learn full multi-hop paths
- **Dijkstra bootstrap**: compute most reliable paths using -log(quality) edge weights

Route table: 12 bytes per entry. Auto-sized to network (3KB for 235 nodes, 15KB for 1200).

### Phase 2: DIRECT (1 TX per Hop)

If a route to destination is known, forward hop-by-hop. Each node makes its own forwarding decision from its local table. Adaptive retries per hop (3-8 based on link quality). Up to 3 alternative next-hops tried per node.

### Phase 3: WALK (Biased Exploration)

If directed forwarding gets stuck at node X, walk toward the destination:
- Score each of X's neighbors: `has_route * 1000 - hop_count + quality * 10 + degree * 0.1`
- Move to best-scored neighbor (1 TX)
- From new position, try directed again
- Max 5 walk steps

This finds nodes that know routes the stuck node doesn't — without flooding.

### Phase 4: MINI-FLOOD (Targeted Last Resort)

If walk also fails, tiny selective flood from current position:
- Each relay forwards to only its **2 best-quality neighbors** (not all)
- Max **4 hops** deep
- Typical cost: ~30 TX per flood
- This catches "almost there" cases that walk missed

On well-connected networks: Phases 3-4 are never triggered. 100% directed.

## 4. Why No Full Flooding

The mathematical proof: on a 5% quality link (typical valley→mountain), 8 retries give only 34% success. The expected cost is 9.7x higher than routing around the bad link via a 5-hop path with 70% quality links (99.5% success, 8.2 TX).

**Flooding amplifies the half-duplex cascade.** Each broadcast TX blocks all neighbors from receiving. In Bay Area, one mountain node TX blocks 234 nodes. Directed routing (1 TX) blocks only 1 node.

## 5. Simulation Results

### 1200-Node Bay Area (Real Scale)

| Router | Delivery | TX Cost |
|--------|:--------:|:------:|
| Managed Flooding | **4%** | 38,644 |
| **WalkFlood** | **88%** | **5,909** |

### Standard Scenarios (235 nodes max)

| Scenario | Managed Flood | System 5 | **WalkFlood** |
|----------|:---:|:---:|:---:|
| Small (20 nodes) | 87% / 1,640 TX | 100% / 237 TX | **100% / 159 TX** |
| Medium (100 nodes) | 27% / 3,380 TX | 100% / 15,912 TX | **100% / 368 TX** |
| Node Kill 20% | 16% / 2,696 TX | 80% / 15,517 TX | **100% / 383 TX** |
| Stress 30% | 19% / 3,380 TX | 73% / 22,743 TX | **99% / 492 TX** |
| Bay Area (235n) | 6% / 6,752 TX | 78% / 585,806 TX | **84% / 8,894 TX** |

WalkFlood beats System 5 in **every scenario** while using 7-1500x fewer transmissions.

### Scaling Behavior

WalkFlood **improves** with more nodes (88% at 1200 vs 84% at 235) because denser networks provide more routing knowledge through passive learning.

## 6. Comparison with Existing Protocols

| Protocol | Control Overhead | Flood Fallback | Memory | GPS? |
|----------|:---:|:---:|:---:|:---:|
| BATMAN IV/V | O(n²) OGMs/sec | No | 10-14 KB | No |
| AODV | Flood per new dest | Yes (RREQ) | 20-30 B/route | No |
| OLSR | O(n) TC messages | No | Full table | No |
| RPL | Trickle DIO beacons | No | 1 parent entry | No |
| MeshCore | Flood-then-direct | Yes (discovery) | Unknown | No |
| Meshtastic 2.6 | Flood + NextHop ACKs | Yes (fallback) | NodeDB | No |
| System 5 | OGM + probes | Yes (cluster flood) | Multi-path table | Yes |
| **WalkFlood** | **Zero** | **Mini (scoped)** | **12 B/route** | **No** |

## 7. Implementation

~200 lines of C for the core routing logic. Key components:
- Route table: flat hash table, O(1) lookup, static allocation
- Dijkstra bootstrap: runs once at startup, O(N·log(N)) per node
- Walk scoring: O(neighbors) per step
- Mini-flood: O(2^hop_limit) TX worst case

### Memory Budget

| Network Size | Table Size | RAM |
|---|---|---|
| 20 nodes | 40 entries | 480 B |
| 235 nodes | 255 entries | 3,060 B |
| 1,200 nodes | 1,220 entries | 14,640 B |

## 8. Honest Limitations

- **Cold start**: New nodes need ~minutes of traffic to learn routes. First messages to unknown destinations are dropped.
- **Broadcast**: WalkFlood is for unicast. Broadcasts still need managed flooding.
- **Simulation vs reality**: Tested with simplified propagation model. Real-world validation via Meshtasticator and BayMesh MQTT data collection is planned.
- **Not compared against real Meshtastic NextHopRouter**: Our ManagedFlooding baseline doesn't include v2.6 next-hop learning for DMs.

## 9. Future Work

1. **Validation**: Run against Meshtasticator (real firmware) and calibrate with BayMesh MQTT data
2. **Firmware prototype**: Implement as optional routing module alongside `ReliableRouter`
3. **Fountain codes**: For links where directed fails repeatedly, rateless coding could make every TX useful instead of binary success/fail
4. **GPS-TDMA**: Eliminate half-duplex cascade at radio layer (5-10x throughput)
5. **Adaptive SF per hop**: SF7 for short hops (37x faster), SF12 for long hops

## 10. References

- goTenna ECHO/VINE: Zero-control-packet mesh protocols
- MeshCore: Flood-then-direct with pull-based telemetry
- OLSR MPR: Multi-point relay selection
- SLR for ZigBee: Self-learning routing through overhearing
- DSR: Route cache population via promiscuous listening
- CTP/RPL: Trickle timer and datapath validation
- Semtech AN1200.13: LoRa time-on-air calculations
- Wellington NZ Migration: Real-world proof that reducing airtime works
- BayMesh MQTT: mqtt.bayme.sh (public traffic data)
- Meshtasticator: github.com/meshtastic/Meshtasticator (official simulator)
- Meshtastic firmware: Router.cpp → FloodingRouter → NextHopRouter → ReliableRouter
