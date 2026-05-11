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

**Node Failure Recovery**: MPR/ECHO backbone nodes dying causes temporary coverage gaps. V6 reconverges via passive learning from subsequent traffic. Worst case: falls back to managed flooding for affected routes. *Mitigation*: Redundant MPR (2-coverage), route expiry timeout forcing re-learn.

**Network Partition & Merge**: Stale routes point to unreachable nodes after partition. When segments merge, conflicting route tables. *Mitigation*: Route expiry (5-10 minutes). On merge, ECHO mechanism naturally recalibrates backbone.

**Mobile Nodes**: V6 neighbor tables go stale when nodes move. MPR sets become invalid. *Mitigation*: Aggressive route expiry for mobile nodes (detect via position delta). Re-compute MPR on neighbor changes.

**Congestion Collapse**: V6 reduces TX by 57-61%, which delays congestion onset but doesn't prevent it. At extreme density (1000+ nodes), even suppressed traffic saturates the channel. *Mitigation*: Combine with Meshtastic's channel utilization gating (25% threshold). GPS-TDMA for deterministic scheduling.

**Graceful Degradation**: When V6 routes expire (no traffic for timeout period), node has empty route table = falls back to managed flooding behavior automatically. Transition is smooth — first packet floods, subsequent packets use learned routes.

**Byzantine Fault Tolerance**: With N% malicious nodes: at 10%, V6 still functions (routes around bad nodes via redundant MPRs). At 30%+, V6 degrades to flooding. Managed flooding is slightly more resilient here because it doesn't trust any specific relay — but it also can't detect or avoid malicious nodes.

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

## Recommended Implementation Order

1. **Now**: Gossip probability + NeighborInfo consumption (low effort, immediate TX reduction)
2. **Next**: Network coding (XOR at relays) — biggest single improvement
3. **Then**: Route authentication (HMAC) + watchdog — security hardening
4. **Later**: GPS-TDMA, hierarchical clustering, SF optimization

---

*Code: [ClemensSimon/Meshtasticator system-v6 branch](https://github.com/ClemensSimon/Meshtasticator/tree/system-v6)*
*Discussion: [meshtastic/firmware#9936](https://github.com/meshtastic/firmware/discussions/9936)*
