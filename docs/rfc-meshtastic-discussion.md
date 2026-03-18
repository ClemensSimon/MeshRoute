# RFC: Geo-Clustered Multi-Path Routing (System 5) — A Proposal to Reduce Transmissions by 90-99%

**Author:** Clemens Simon
**Repository:** [ClemensSimon/MeshRoute](https://github.com/ClemensSimon/MeshRoute)
**Live Simulator:** [Try it here](https://clemenssimon.github.io/MeshRoute/simulator.html)
**Status:** Research prototype with working simulator + ESP32 firmware

---

## The Problem

Meshtastic's managed flooding is clever — SNR-based suppression, ROUTER roles, congestion scaling. But it still scales as **O(n) per message per hop**. Every node that hears the message rebroadcasts it. This is why the **hop limit (3-7) exists**: without it, a single message would consume the entire network's airtime.

This means:
- A 100-node network uses **200,000+ transmissions** for 100 messages
- A 1500-node network can only deliver **36%** of messages (congestion collapse)
- Networks can't grow beyond ~100 nodes without severe performance degradation

## The Proposal: System 5

**Core idea:** Instead of flooding, route messages along **pre-computed paths** through **geo-clustered** nodes. Each hop costs exactly **1 TX** instead of N.

### How it works

1. **Geo-Clustering**: Nodes self-organize into clusters based on GPS geohash. Nearby nodes share a cluster.
2. **Border Nodes**: Nodes with neighbors in other clusters become gateways (like OSPF area border routers).
3. **Route Tables**: Each node maintains a small routing table with multiple paths to known destinations.
4. **Directed Routing**: Messages follow the best path — 1 TX per hop, no broadcasting.
5. **Scoped Fallback**: If all routes fail, flood only within SRC + DST clusters (not the whole network).

### The key difference

| Approach | Cost per Message | Hop Limit Needed? |
|----------|:----------------:|:-----------------:|
| Managed Flooding | O(n) per hop | Yes (3-7) |
| Next-Hop (v2.6 DMs) | O(n) first msg, then ~1 | Partially |
| **System 5** | **O(hops) always** | **No** |

20 hops with System 5 costs less than 1 hop of managed flooding.

## Benchmark Results

I built a simulator that compares 4 routing algorithms (Naive Flooding, Managed Flooding, Next-Hop, System 5) on identical networks with identical messages. **22 scenarios** from 20-node local mesh to 1500-node metro scale.

### Where System 5 dominates (100% delivery, 90-99% fewer TX)

| Scenario | Managed TX | System 5 TX | Savings |
|----------|----------:|------------:|--------:|
| Medium City (100 nodes) | 201,920 | 5,678 | **97.2%** |
| Festival (100 nodes, mobile) | 912,953 | 107 | **99.99%** |
| Building Emergency (200 nodes) | 3,651,559 | 386 | **99.99%** |
| Community Mesh (80 nodes) | 75,991 | 88 | **99.9%** |
| Rural Long Range | 30,152 | 287 | **99.0%** |

### Where it gets interesting (large scale)

| Scenario | Managed Del% | S5 Del% | Comment |
|----------|:-----------:|:-------:|---------|
| 1000 Nodes (40km) | 47% | 45% | Both struggle — S5 uses 2% fewer TX |
| **1500 Nodes (50km)** | **36%** | **51%** | **S5 delivers MORE than flooding!** |

At metro scale, managed flooding collapses from congestion. System 5's directed routing avoids the congestion entirely.

### Honest about weaknesses

| Scenario | Managed Del% | S5 Del% | Why |
|----------|:-----------:|:-------:|-----|
| Mountain Valley | 3% | 5% | Both fail — network is physically broken |
| Partition Recovery (40% dead) | 88% | 56% | S5 routes break, fallback covers ~half |
| 50% Degraded Links | 100% | 73% | Flooding is more resilient to random link failures |

**System 5 is not always better.** In heavily degraded networks, flooding's redundancy wins. The solution: System 5 with managed flooding fallback (which is exactly how the prototype works).

## Backward Compatibility

**The #1 concern**: Can System 5 nodes coexist with existing Meshtastic nodes?

**Yes.** The prototype implements dual-mode:
- **S5 → S5**: Directed routing (1 TX per hop, no flooding)
- **S5 → Legacy**: Normal managed flooding (full backward compatibility)
- **Legacy → S5**: S5 node receives via flooding, routes directed from there
- **S5 nodes ignore the hop limit** (they don't flood, so no broadcast storm risk)
- **Legacy nodes prefer S5 neighbors** when flooding (reaches S5 infrastructure faster)

The [live simulator](https://clemenssimon.github.io/MeshRoute/simulator.html) has 5 mixed-mode scenarios (10%–90% S5) where you can see this in action hop-by-hop.

## What I've Built

### 1. Interactive Simulator ([try it](https://clemenssimon.github.io/MeshRoute/simulator.html))
- Side-by-side comparison: Managed Flooding vs System 5
- **Step hop-by-hop** to see exactly what happens
- 18 scenarios including mixed-mode backward compatibility
- Explains at each step WHY System 5 knows the right path

### 2. Python Benchmark
- 22 scenarios, 4 routers, 100 messages each
- Terrain models (rural to indoor), mobile nodes, asymmetric links
- Multiprocessing across all CPU cores
- `pip install` nothing — pure Python

### 3. ESP32 Firmware (standalone, no Meshtastic fork needed)
- Supports **Heltec V3**, **T-Beam**, **RAK4631**
- OGM neighbor discovery, directed routing, managed flooding fallback
- GPS + RSSI triangulation for boards without GPS
- Serial CLI for testing
- ~8KB RAM for routing state

## What I'm Asking For

1. **Feedback on the approach** — Does this make sense for Meshtastic? What am I missing?
2. **Edge cases** — What real-world scenarios would break this?
3. **Integration path** — Would a FloodingRouter subclass (like NextHopRouter) be the right way to integrate?
4. **Testing partners** — Anyone want to test with real hardware? The firmware runs on common boards.

## Links

- **Repository**: https://github.com/ClemensSimon/MeshRoute
- **Live Simulator**: https://clemenssimon.github.io/MeshRoute/simulator.html
- **Presentation**: https://clemenssimon.github.io/MeshRoute/
- **Benchmark Results**: https://github.com/ClemensSimon/MeshRoute/blob/main/simulator/results.json

The code is MIT licensed. I'm happy to contribute this upstream if there's interest.
