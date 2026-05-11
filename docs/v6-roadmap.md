# MeshRoute System V6 — Research & Roadmap

*Consolidated findings from three parallel research tracks (11.05.2026)*

## Current Status (Meshtasticator Benchmark)

| Version | TX Reduction | Collision Reduction | Mechanism |
|---|:---:|:---:|---|
| V6 v1 | 10-35% | 10-35% | Passive route learning, basic suppression |
| V6 v2 | 30-40% | 35% | + Deferred rebroadcast, TX power control |
| **V6 v3** | **57-61%** | **41%** | + MPR relay selection, ECHO backbone |

V6@7hops costs less than 1/3 of MF@3hops with comparable reach.

---

## Part 1: Optimization Opportunities (Ranked by Impact/Complexity)

### Tier 1 — High Impact, Low Complexity (Implement Now)

**1. Network Coding (XOR at Relays) — 33-55% additional TX reduction**
Relay combines two overheard packets via XOR into one TX. Recipients who have one packet extract the other. Studied specifically for LoRa (arXiv 2109.06018). Trivial computation (XOR), main challenge: tracking what neighbors have received.

**2. Gossip Probabilistic Forwarding — 25-35%**
Forward with probability p = k/active_neighbors instead of always. One random-number check before rebroadcast. Self-adaptive to density. Can combine with MPR (gossip among non-MPR nodes, deterministic for MPRs).

**3. NeighborInfo as MPR Data Source (Meshtastic-native)**
Meshtastic already broadcasts 1-hop neighbor lists with SNR via NeighborInfo module. Receiving these gives 2-hop topology for free — exactly what MPR needs. Zero extra airtime. V6 should consume NeighborInfo packets to build MPR sets.

**4. relay_node Header Field for MPR Signaling**
Meshtastic 2.6 added `relay_node` (1 byte) to the OTA header. V6 can use this: only MPR-designated nodes set relay_node=self before rebroadcast. Non-MPRs see relay_node set by others and suppress.

### Tier 2 — High Impact, Medium Complexity

**5. GPS-TDMA Slot Assignment — 50-80% collision elimination**
GPS-synchronized time slots eliminate all collision-caused retransmissions. Most Meshtastic boards have GPS. Edge Orbital's Tessera demonstrates this is production-ready. Main challenge: dynamic slot assignment.

**6. Position+Energy-Aware Routing — 75% TX reduction**
arXiv 2510.03714 (Oct 2025): Specifically for LoRa mesh. Standby repeaters passively monitor, intervene on failure. Battery info piggybacked on data packets. 185% throughput increase in tests.

**7. Hierarchical Clustering + Data Aggregation — 60-80% for telemetry**
LEACH-style: 10 nodes report to 1 cluster head, which sends 1 aggregated packet. Massive reduction for position/telemetry (98% of Meshtastic traffic). Rotation prevents battery drain.

**8. Ant Colony Optimization (ACO) — 20-40%**
Pheromone trails as route quality metric. Successful deliveries reinforce paths, failures decay them. Natural load balancing. BLE mesh + ACO hybrid shown in 2025.

### Tier 3 — Game-Changer, High Complexity

**9. Collision Decoding (CIC) — 10x capacity**
Microsoft Research: decode colliding LoRa packets via signal processing. Makes collision avoidance unnecessary. Requires custom SDR gateway.

**10. SF Orthogonality — 2-6x parallel capacity**
Different SFs on same frequency are quasi-orthogonal. Assign SFs by tier (short-range=SF7, long-range=SF12). Both transmit simultaneously.

**11. Compressed Sensing for Telemetry — 50-80%**
Nodes transmit random linear combinations of their data. Gateway reconstructs all readings from M << N transmissions. Ideal for position aggregation.

**12. Fountain/Raptor Codes for Broadcast — 40-60%**
Rateless codes: receiver reconstructs from any sufficient subset. No ACK/retransmission needed for firmware updates or group messages.

---

## Part 2: Security Analysis

### CRITICAL

**Route Poisoning (Severity: CRITICAL)**
V6 learns routes from overheard packets based purely on RSSI. Attacker at high power poisons all routes to point through itself. No authentication of origin claims.
- *Mitigation*: HMAC signatures using channel PSK. Route trust scoring over time. Sequence number validation (routes only update if seq is newer).

### HIGH

**MPR Manipulation (Severity: HIGH)**
Attacker forges 2-hop topology claims to get elected as MPR, becoming man-in-the-middle.
- *Mitigation*: Redundant MPR sets (2-coverage). Monitor MPR forwarding via ECHO score. Rate-limit topology learning.

**Selective Forwarding / Blackhole (Severity: HIGH)**
Node agrees to relay but silently drops packets. ECHO mechanism doesn't detect this (it monitors own rebroadcasts, not upstream behavior).
- *Mitigation*: Watchdog mechanism — listen for next-hop's retransmission. Per-neighbor reliability score. Remove suspect from routes after N failures.

**Sybil Attack (Severity: HIGH)**
One device pretends to be many nodes. Inflates neighbor tables, corrupts MPR election, fills NodeDB (limited to ~100 entries).
- *Mitigation*: Neighbor rate limiting. Activity validation (require sustained behavior). Leverage PKI for favorited nodes. RSSI-based triangulation.

### MEDIUM

**Replay Attacks (Severity: MEDIUM)**
Replayed packets are suppressed by dupe detection, BUT route table updates based on RSSI still fire.
- *Mitigation*: Check sequence number freshness before route updates. Expire routes after configurable timeout.

**Privacy / Tracking (Severity: MEDIUM)**
Route tables + position packets = movement tracking. V6's topology knowledge amplifies this.
- *Mitigation*: Periodic NodeID rotation. Position precision reduction for broadcasts.

---

## Part 3: Resilience Analysis

**CRITICAL MISSING FEATURE: Route/Neighbor Expiry.** V6 currently never removes stale routes or neighbors. This single gap causes 5 of the resilience issues below. Adding a configurable TTL (e.g., 300s) to all route and neighbor entries is the highest-priority fix.

**Node Failure Recovery (HIGH)**: MPR/ECHO backbone nodes dying causes silent packet loss until MPR recomputation triggers (every 50 observations). ECHO mechanism can make it worse by self-suppressing nodes that lost their relay. *Fix*: Neighbor timeout triggers immediate MPR recomputation. Redundant MPR (2-coverage).

**Network Partition & Merge (HIGH)**: Stale routes persist from before partition. After merge, conflicting route tables produce suboptimal paths. *Fix*: Route expiry (5 min). On merge: flush entries when many new NodeIDs suddenly appear.

**Mobile Nodes (HIGH)**: V6 stores stale RSSI/relay data for moved nodes. A driving node exits cluster range in seconds. *Fix*: Age-out neighbors not heard within 2x position update interval. RSSI-based route validity check.

**Congestion Collapse (HIGH)**: V6 delays collapse but doesn't prevent it. At 50 nodes with 3-hop flooding, raw rebroadcast rate (7.5 pkt/s) exceeds channel capacity (2 pkt/s). V6 must achieve >75% suppression to avoid collapse. *Fix*: Channel utilization-aware suppression (already available via `channelUtilizationPercent()`). Backpressure signaling.

**Graceful Degradation (MEDIUM)**: Fresh start → clean flooding fallback (correct). Partially stale state → dangerous intermediate where old MPR/ECHO data causes packet loss. *Fix*: Confidence metric: bypass MPR/ECHO suppression when fewer than N fresh observations exist.

**Byzantine Fault Tolerance (HIGH)**: V6 is LESS Byzantine-tolerant than managed flooding because MPR concentrates trust. At 10% malicious nodes: ~27% chance per node of having compromised MPR. At 20%: ~49%. *Fix*: Redundant MPR (2-coverage) + probabilistic forwarding fallback (non-MPR nodes forward at 10% probability).

### Real-World Failure Incidents (Meshtastic)

| Incident | Root Cause | V6 Impact |
|---|---|---|
| **Hamvention 2024** | Single MQTT bridge flooded network | V6 reduces amplification, but MQTT injection bypasses suppression |
| **DEF CON 33 (2000+ nodes)** | NodeDB eviction (100 entry limit), NodeInfo spoofing | V6 neighbor tables equally vulnerable to eviction |
| **CVE-2025-24798** | Crafted routing packet crashes firmware | V6 route learning code paths need fuzz-testing |
| **v2.5 bidirectional failure** | HAM mode disabled PKC, breaking DM return path | V6 should validate bidirectional reachability |

---

## Part 4: Meshtastic Integration Points

| V6 Feature | Meshtastic Hook | Effort |
|---|---|---|
| MPR relay selection | `perhapsRebroadcast()` in NextHopRouter.cpp | Medium |
| ECHO backbone | `wasSeenRecently()` + new echo bit in flags | Medium |
| NeighborInfo consumption | NeighborInfoModule callbacks | Low |
| TX power control | `txpow` in RadioInterface | Low |
| relay_node as MPR signal | Already in OTA header (v2.6) | Low |
| Channel util adaptive suppression | `channelUtilizationPercent()` | Low |
| Route expiry | Timer in node route table | Low |

**EU868 Duty Cycle Benefit**: V6's 60% TX reduction = 3% freed duty cycle on a 5% budget. Prevents silent periods in chatty meshes. Pushes channel utilization below the 25% polite threshold, restoring normal position/telemetry operation.

---

## Top 5 Priority Fixes (Security + Resilience)

These must be implemented before any further optimization:

1. **Route/Neighbor Expiry** — Resolves 5 resilience issues (node failure, partition, mobile, degradation, privacy). Single highest-impact fix. Add TTL to all v6_routes and v6_neighbors entries.

2. **Header Authentication (HMAC)** — Resolves route poisoning (CRITICAL), MPR manipulation, replay attacks. Derive HMAC key from channel PSK (zero-cost, PSK already available). Include in encrypted payload covering header fields.

3. **Watchdog Mechanism** — Detects blackhole nodes and Byzantine MPRs. After forwarding, listen for next-hop's retransmission. Maintain per-neighbor reliability score. Demote unreliable relays.

4. **Redundant MPR (2-coverage)** — Tolerate single MPR failure. Select 2 independent MPR sets. Probabilistic fallback: non-MPR nodes forward at 10% probability for baseline resilience.

5. **Channel Utilization-Aware Suppression** — Prevent congestion collapse. Use existing `channelUtilizationPercent()` to dynamically increase suppression when channel is busy (>25% → strict MPR only, >40% → suppress all non-essential).

## Recommended Implementation Order

1. **Now**: Route expiry + gossip probability + NeighborInfo consumption
2. **Next**: HMAC authentication + watchdog mechanism
3. **Then**: Network coding (XOR at relays) — biggest TX optimization
4. **Later**: GPS-TDMA, hierarchical clustering, SF optimization

---

*Code: [ClemensSimon/Meshtasticator system-v6 branch](https://github.com/ClemensSimon/Meshtasticator/tree/system-v6)*
*Discussion: [meshtastic/firmware#9936](https://github.com/meshtastic/firmware/discussions/9936)*
