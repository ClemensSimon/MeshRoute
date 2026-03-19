# [Feature Request]: Directed Multi-Path Routing to Replace Hop Limit Dependency

## Platform

Cross-Platform

## Description

### Problem / Motivation

Meshtastic's managed flooding (v2.6/2.7) works well for small networks but has a fundamental scaling limitation: **each hop multiplies transmissions proportional to network size**. This forces a hop limit (default 3–7), which doesn't just cap range — it **prevents delivery** at scale.

Simulation with realistic hop limits reveals the problem clearly:

| Network Size | Hop Limit 3 | Hop Limit 5 | Hop Limit 7 |
|-------------|------------|------------|------------|
| 20 nodes | 100% delivery | 100% | 100% |
| 100 nodes | 92% | 100% | 100% |
| 500 nodes | **14%** | **31%** | **51%** |
| 1000 nodes | **2%** | **6%** | **6%** |
| 1500 nodes | **2%** | **4%** | **5%** |

At 1000 nodes with the maximum hop limit of 7, only **6 out of 100 messages arrive**. Raising the hop limit doesn't help — it just adds more flooding transmissions without meaningfully improving delivery.

This matters for:
- **Emergency/disaster networks** (80 nodes, 10km): managed flooding delivers only 62–84% depending on hop limit
- **Rural/maritime deployments** (30–50 nodes, spread out): 69–88% delivery
- **Growing community meshes** heading toward 500+ nodes

### Proposed Solution: System 5 — Directed Multi-Path Routing

A routing protocol where **each hop costs ~1 transmission** instead of proportional to network size. The hop limit becomes irrelevant.

**Core mechanisms:**

1. **Geo-Clustering** (borrowed from OSPF Areas) — Nodes self-organize by GPS geohash prefix into clusters of ~50 nodes. Full topology within clusters, summarized routes between. Clusters subdivide recursively as the network grows.

2. **OGM-Based Quality Metric** (borrowed from B.A.T.M.A.N.) — Periodic originator messages. Count reception rate per neighbor. No complex calculation — just count arrivals.

3. **Multi-Path Weighted Routing** (borrowed from data center ECMP) — 2–3 pre-computed paths per destination. Traffic distributed proportionally via weight function:
   ```
   W(route) = 0.4 × LinkQuality + 0.35 × (1 - Load) + 0.25 × MinBattery
   ```
   Good paths get more traffic, but never all. No single bottleneck node.

4. **Back-Pressure** — Overloaded nodes report queue pressure (piggybacked, 2 bytes). Traffic automatically shifts away from congested paths.

5. **Adaptive QoS** — Local Network Health Score (NHS) per cluster gates traffic by priority. SOS (P0) always passes, even at 1% network health. Firmware updates (P7) only when the network is healthy.

6. **Fallback** — If all routes fail, scoped cluster flooding (source + destination cluster only, not entire network). Corridor-based: BFS on cluster graph to find the narrowest path.

**Hardware feasibility:**
- Routing table: ~5 entries per destination × 3 routes = 15 entries. At 200 nodes: ~3 KB RAM.
- OGM overhead: 1 beacon per 30s, 20 bytes. At 50 neighbors: ~33 bytes/s.
- Cluster computation: runs once at boot + on topology change. O(n) per cluster.
- All operations are integer arithmetic — no floating point required on ESP32.

### Simulation Results

Python simulator with EU868 LoRa model, 21 scenarios, 6 routers (Naive Flood, Managed Flood at 3/5/7 hops, Next-Hop, System 5), identical network topologies:

**Small-to-medium networks (sweet spot):**

| Scenario | Nodes | Managed 7h TX | System 5 TX | Delivery | TX Savings |
|----------|-------|--------------|-------------|----------|-----------|
| Small Local | 20 | 16,459 | 115 | 100% / 100% | 99.3% |
| Medium City | 100 | 201,920 | 402 | 100% / 100% | 99.8% |
| Dense Urban | 200 | 1,490,555 | 105,320 | 100% / 100% | 92.9% |
| Festival | 150 | 912,953 | 107 | 100% / 100% | ~100% |
| Duty Cycle | 100 | 404,779 | 918 | 100% / 100% | 99.8% |

**Large networks (honest about tradeoffs):**

| Scenario | Nodes | Flood 7h Del. | Sys5 Del. | Flood 7h TX | Sys5 TX | TX/Delivered |
|----------|-------|--------------|-----------|-------------|---------|-------------|
| Regional | 500 | 51% | **76%** | 435,552 | 412,302 | 8,540 vs 5,425 |
| 1000 nodes | 1000 | 6% | **43%** | 78,387 | 181,855 | 13,065 vs 4,229 |
| 1500 nodes | 1500 | 5% | **42%** | 80,912 | 197,202 | 16,182 vs 4,695 |

At 1000+ nodes, System 5 uses **more total TX** than managed flooding — but delivers **7x more messages**. The cost per delivered message is 3x lower.

**Stress scenarios:**

| Scenario | Managed 7h Del. | Sys5 Del. | Managed 7h TX | Sys5 TX |
|----------|----------------|-----------|--------------|---------|
| 30% degraded links | 100% | 73% | 208,164 | 11,866 |
| 50% degraded links | 100% | 73% | 215,372 | 15,772 |
| 20% nodes killed | 100% | 80% | 132,780 | 4,072 |
| 40% killed + degraded | 78% | 58% | 58,038 | 20,646 |

Under link degradation, managed flooding's delivery advantage comes at **14–18x the TX cost**. Under severe node loss, both protocols struggle.

**Known weakness:** Mountain Valley (60 nodes, poor propagation, 88 links) — 2% delivery for both protocols. When the physical network is too sparse, no routing algorithm helps.

### Try It

- **Live interactive demo:** https://clemenssimon.github.io/MeshRoute/
- **GitHub (simulator + full docs):** https://github.com/ClemensSimon/MeshRoute
- MIT licensed

To reproduce: Open the demo → Simulation Results section shows all 21 scenarios with interactive charts. The Approaches section shows side-by-side animations of all four routing strategies on identical topology.

### Comparison: Current vs. Proposed

| Aspect | Managed Flooding (current) | System 5 (proposed) |
|--------|--------------------------|-------------------|
| TX per message | O(n) — proportional to network size | O(hops) — proportional to path length |
| Hop limit | Required (3–7), caps range | Not needed — 20 hops cost less than 1 flooded hop |
| Delivery at 500 nodes | 51% (7 hops) | 76% |
| Delivery at 1000 nodes | 6% | 43% |
| Broadcasts | Floods entire network | Directed, same as unicast |
| Load balancing | None | Weighted proportional across 2–3 paths |
| Failover | Implicit (redundant flooding) | Instant switch to cached backup path |
| GPS required | No | Yes (for geo-clustering) |
| Complexity | Low | Medium-high |

### Implementation Plan (if community is interested)

1. **Phase 1: Optional routing module** — Implement `System5Router` alongside existing `FloodingRouter` in the firmware. Selectable via device config (like `role` selection). No changes to existing flooding behavior.

2. **Affected modules:**
   - `src/mesh/Router.h/cpp` — New `System5Router` class inheriting from `Router`
   - `src/mesh/NodeDB.h/cpp` — Add routing table, cluster assignment, NHS fields
   - `src/mesh/MeshService.h/cpp` — OGM beacon scheduling
   - New: `src/mesh/GeoCluster.h/cpp` — Cluster management
   - New: `src/mesh/RouteTable.h/cpp` — Multi-path route storage

3. **Feature flag:** `config.lora.routing_mode = SYSTEM5` (default: `FLOODING` unchanged)

4. **Backward compatibility:** System 5 nodes can coexist with flooding nodes. A System 5 node receiving a flooded packet processes it normally. A flooding node receiving a directed packet treats it as a normal unicast. No protocol-level incompatibility.

### Risks & Mitigations

- **GPS requirement:** Geo-clustering needs GPS. Mitigation: neighbor consensus fallback (if 4/5 neighbors report a geohash, adopt it). Nodes without GPS or neighbors: "homeless" mode with local flooding.
- **Mixed firmware networks:** System 5 falls back to scoped flooding when the next hop doesn't support directed routing. Gradual migration works.
- **Memory overhead:** ~3 KB for routing tables at 200 nodes. ESP32 has 520 KB SRAM — well within budget.
- **Worst case:** Very sparse networks (Mountain Valley scenario) — System 5 performs equal to flooding, not worse. The fallback to scoped flooding ensures no regression.
- **Kill switch:** `config.lora.routing_mode = FLOODING` reverts to current behavior instantly.

### Discussion

This started as a [GitHub Discussion #9936](https://github.com/meshtastic/firmware/discussions/9936) with initial community feedback. The simulation has since been updated with realistic hop limits and improved algorithms (dynamic clustering, corridor-based fallback).

Questions for the team:
1. Is this level of routing complexity acceptable for the Meshtastic firmware?
2. Should the GPS requirement be a hard blocker, or is the neighbor consensus fallback sufficient?
3. Would you prefer a single large PR or incremental steps (clustering first, then routing, then QoS)?
