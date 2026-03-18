# MeshRoute

**A geo-clustered multi-path routing proposal for Meshtastic — benchmarked against Meshtastic's actual managed flooding (v2.6/2.7).**

**[Live Demo](https://clemenssimon.github.io/MeshRoute/)** — Interactive presentation with algorithm visualizations, simulation results, and resilience testing.

**[Live Simulator](https://clemenssimon.github.io/MeshRoute/simulator.html)** — Step-by-step side-by-side comparison of Managed Flooding vs System 5.

## Live Simulator

![MeshRoute Simulator — Managed Flooding vs System 5](docs/simulator-screenshot.png)

*Left: Managed Flooding (Meshtastic) — 169 TX to flood 4 hops, every node rebroadcasts blindly. Right: System 5 (MeshRoute) — 4 TX along a direct path through cluster bridge nodes. Same network, same message, same hop count.*

The simulator lets you:
- **Step hop-by-hop** through both routing algorithms simultaneously
- **See the difference visually** — yellow highlights show where each message has reached
- **Click nodes** to choose source and destination
- **Switch scenarios** — from small local mesh to disaster relief with 40% node loss
- **Read explanations** of what happens at each hop and why System 5 knows the right path

## Meshtastic's Current Routing (v2.6/2.7)

Meshtastic does **not** use naive flooding. Its actual routing is already quite clever:

- **Managed Flooding** (all messages): Before rebroadcasting, nodes listen briefly. If they hear another node rebroadcast first, they suppress. SNR-based priority gives distant nodes shorter contention windows so they rebroadcast first. ROUTER/ROUTER_LATE roles always rebroadcast. This suppresses ~40-50% of transmissions vs. naive flooding.
- **Next-Hop Routing** (direct messages since v2.6): First message floods. The system learns which relay succeeded. Subsequent DMs go only via that cached relay node. Falls back to managed flooding if the next-hop fails.
- **Congestion Scaling** (v2.6+): Networks with 40+ nodes automatically stretch broadcast intervals using `ScaledInterval = Interval * (1 + (Nodes - 40) * 0.075)`.

**The limitation:** Both approaches still scale as O(n) per message. The hop limit (3-7) remains necessary because each hop multiplies transmissions proportional to network size.

## What System 5 Proposes

A routing protocol that achieves **O(hops) cost** instead of O(n) — for all message types, not just DMs.

**Geo-Clustering** — Nodes self-organize by GPS geohash. Full topology within clusters, border nodes between.

**Multi-Path Routing** — 2-3 cached paths per destination. Instant failover without rediscovery.

**Weighted Load Balancing** — `W(r) = α·Q(r) + β·(1-Load(r)) + γ·Batt(r)`. Traffic distributed proportionally across paths.

**Adaptive QoS** — Network Health Score per cluster throttles low-priority traffic under stress. SOS always gets through.

**Fallback** — Scoped cluster flooding (not full network) when routes fail.

## The Key Difference: ~1 TX per Hop

| Approach | Cost per Message | Cost per Hop | Hop Limit Needed? |
|----------|:----------------:|:------------:|:-----------------:|
| Managed Flooding | O(n) * (1-S) | n * (1-S) | Yes (3-7) |
| Next-Hop (DMs) | O(hops) after learning | ~1 | Partially |
| **System 5** | **O(hops) always** | **~1** | **No** |

This makes the hop limit irrelevant: 20 hops cost less than managed flooding costs for 1.

## Simulation Results (vs. Meshtastic's Actual Routing)

The simulator compares all four approaches on identical networks:

### Normal Conditions

| Scenario | Nodes | Managed Flood TX | System 5 TX | S5 vs Managed |
|----------|------:|-----------------:|------------:|--------------:|
| Small Local (1km) | 20 | 17,045 | 112 | **99.3% less** |
| Medium City (5km) | 100 | 155,774 | 196 | **99.9% less** |
| Large Regional (20km) | 500 | 708,720 | 497 | **99.9% less** |
| Dense Urban (3km) | 200 | 1,239,692 | 136 | **100% less** |
| 1000 Nodes (40km) | 1000 | 1,462,489 | 10,635 | **99.3% less** |
| 1500 Nodes (50km) | 1500 | 2,119,189 | 38,850 | **98.2% less** |

### Stress Tests

| Scenario | Managed Flood TX | System 5 TX | S5 Delivery | S5 vs Managed |
|----------|----------------:|------------:|------------:|--------------:|
| 30% Degraded Links | 170,214 | 4,701 | 100% | **97.2% less** |
| 50% Degraded Links | 183,019 | 16,055 | 73% | **91.2% less** |
| 20% Nodes Killed | 108,413 | 2,853 | 85% | **97.4% less** |
| Combined Stress | 139,491 | 3,427 | 79% | **97.5% less** |

Even against Meshtastic's managed flooding (which already suppresses ~50% of rebroadcasts), System 5 saves **91-99.9% of transmissions**.

## Interactive Presentation

Open `index.html` in a browser. No build step, no dependencies.

- **Four algorithm animations** on identical topology: Naive Flooding, Managed Flooding (Meshtastic current), Next-Hop (v2.6), System 5
- **Simulation results** with interactive charts and log/linear toggle
- **Step-by-step formation animation**
- **Three scale scenarios** — local, continental, global
- **Interactive resilience testing** — click nodes to kill them
- **QoS priority gate** with real-time NHS gauge

## Running the Simulator

```bash
cd simulator
python run.py                          # run all 22 scenarios (4 routers each)
python run.py --scenario 2             # single scenario
python run.py --parallel scenarios     # parallel across all CPU cores
python run.py --visualize              # ASCII network topology
```

### Scenarios (22 total)

| Category | Scenarios |
|----------|-----------|
| **Scale** | Small Local (20), Medium City (100), Large Regional (500), Dense Urban (200), 1000 Nodes, 1500 Nodes |
| **Stress** | 30% / 50% degraded links, 20% node failure, combined stress |
| **Terrain** | Rural Long Range (SF12), Hiking Trail (linear), Mountain Valley, Maritime (line of sight) |
| **Mobile** | Festival/Event (dense + mobile), Building Emergency (indoor), Highway Convoy |
| **Realistic** | Community Mesh (stable), Indoor-Outdoor Mix, Disaster Relief, Partition Recovery |
| **Regulation** | Duty Cycle Stress (1% EU868 enforced) |

### Simulator Architecture

```
simulator/
  run.py          — CLI entry point (parallel execution support)
  meshsim.py      — Network simulation (nodes, links, clusters, routes, mobility)
  routing.py      — NaiveFloodingRouter, ManagedFloodingRouter,
                    NextHopRouter, System5Router
  lora_model.py   — EU 868MHz LoRa model (terrain, duty cycle, collisions)
  geohash.py      — Geographic clustering via geohash
  benchmark.py    — 22 scenarios, 4 routers, multiprocessing benchmark
```

## Architecture Origins

| Source | Concept | Used As |
|--------|---------|---------|
| Internet (OSPF) | Area-based hierarchy | Geo-clusters with border nodes |
| Freifunk (B.A.T.M.A.N.) | OGM counting | Link quality metric |
| Data Centers (ECMP) | Weighted multi-path | Proportional load distribution |
| Network Theory | Back-pressure | Congestion avoidance |
| Ant Colony Optimization | Pheromone decay | Self-optimizing route weights |
| DNS | Hierarchical cache | Scoped node discovery |

## Project Status

Research project with working simulator. No firmware implementation yet.

- [x] Algorithm design and mathematical analysis
- [x] Interactive presentation with live visualizations
- [x] Python simulator (4 routers, 22 scenarios, EU868 LoRa model)
- [x] Fair comparison against Meshtastic v2.6/2.7 actual routing
- [x] Interactive live simulator with hop-by-hop stepping
- [ ] Firmware prototype (ESP32 / Meshtastic fork)
- [ ] Field testing with real LoRa hardware
- [ ] RFC / proposal to Meshtastic community

## License

MIT — see [LICENSE](LICENSE)

## Author

[Clemens Simon](https://github.com/ClemensSimon)
