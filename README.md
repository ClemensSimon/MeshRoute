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

## Simulation Results (22 Scenarios, 4 Routers)

The simulator compares all four approaches on identical networks (100 messages each):

### Scale Tests

| Scenario | Nodes | Managed Flood TX | System 5 TX | S5 Del% | S5 vs Managed |
|----------|------:|-----------------:|------------:|--------:|--------------:|
| Small Local (1km) | 20 | 16,459 | 115 | 100% | **99.3% less** |
| Medium City (5km) | 100 | 201,920 | 34,382 | 94% | **83.0% less** |
| Large Regional (20km) | 500 | 1,002,437 | 393,891 | 25% | **60.7% less** |
| Dense Urban (3km) | 200 | 1,490,555 | 481,310 | 100% | **67.7% less** |

### Realistic Environments

| Scenario | Managed TX | System 5 TX | S5 Del% | S5 vs Managed |
|----------|----------:|------------:|--------:|--------------:|
| Rural Long Range | 30,152 | 617 | 100% | **98.0% less** |
| Hiking Trail (linear) | 28,894 | 627 | 100% | **97.8% less** |
| Maritime / Coastal | 9,319 | 339 | 100% | **96.4% less** |
| Festival / Event | 912,953 | 107 | 100% | **99.99% less** |
| Building Emergency | 3,651,559 | 386 | 100% | **99.99% less** |
| Highway Convoy | 52,072 | 189 | 100% | **99.6% less** |
| Community Mesh | 75,991 | 88 | 100% | **99.9% less** |
| Indoor-Outdoor Mix | 268,468 | 37,522 | 97% | **86.0% less** |

### Stress Tests

| Scenario | Managed TX | System 5 TX | S5 Del% | S5 vs Managed |
|----------|----------:|------------:|--------:|--------------:|
| 30% Degraded Links | 208,164 | 37,159 | 67% | **82.1% less** |
| 50% Degraded Links | 215,372 | 51,519 | 63% | **76.1% less** |
| 20% Nodes Killed | 132,780 | 15,733 | 74% | **88.2% less** |
| Combined Stress | 170,094 | 34,036 | 72% | **80.0% less** |
| Disaster Relief | 35,635 | 271 | 78% | **99.2% less** |
| Mountain Valley | 747 | 1,036 | 4% | *worse* |
| Partition Recovery | 62,773 | 25,397 | 29% | **59.6% less** |

### Key Findings

- **Dense/medium networks**: System 5 saves **83-99.99%** of transmissions with near-perfect delivery
- **Stress conditions**: Still saves 60-99% TX, but delivery drops to 63-78% (fallback to cluster flooding helps)
- **Extreme conditions** (mountain, partition): System 5 struggles — routes break faster than flooding adapts. These scenarios need the fallback mechanism.
- **Backward compatibility**: Mixed-mode (S5 + Legacy nodes) works — S5 nodes route directly where possible, flood normally for legacy compatibility

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
