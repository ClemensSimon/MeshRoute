# MeshRoute

**Replacing Meshtastic's naive flooding with intelligent geo-clustered multi-path routing.**

**[Live Demo](https://clemenssimon.github.io/MeshRoute/)** — Interactive presentation with algorithm visualizations, resilience testing, and scale scenarios.

Meshtastic uses blind flooding: every node rebroadcasts every message. One message to one recipient causes *n* transmissions across the entire network. With LoRa's constraints (1-50 kbps, 1% EU duty cycle, half-duplex), this collapses beyond ~100 nodes.

MeshRoute proposes **System 5**, a routing protocol that combines ideas from OSPF, B.A.T.M.A.N., ECMP, ant colony optimization, and DNS into one self-healing system.

## How System 5 Works

**Geo-Clustering** — Nodes self-organize into geographic clusters using geohash prefixes. Within a cluster, every node knows the full topology. Between clusters, only border nodes communicate. This reduces routing table size from O(n) to O(cluster_size + border_nodes).

**Multi-Path Routing** — Each source maintains 2-3 independent paths to every destination. When the primary path fails, the next cached path activates instantly — no rediscovery flood needed.

**Weighted Load Balancing** — Traffic is distributed proportionally across paths using:

```
W(r) = alpha * Q(r)  *  beta * (1 - Load(r))  *  gamma * Batt(r)
```

where Q = link quality (B.A.T.M.A.N. OGM reception rate), Load = queue pressure, Batt = minimum battery along route. Good paths get more traffic, but never all — preventing the "ant highway" problem.

**Adaptive QoS** — A Network Health Score (NHS) per cluster controls which traffic classes are allowed. When the network is stressed, low-priority traffic is automatically throttled. SOS (priority 0) always gets through, even at 1% NHS. The network "breathes": less traffic under stress leads to recovery, which allows more traffic again — a stable negative feedback loop.

**Back-Pressure** — Overloaded nodes (queue > 80%) are automatically avoided in route selection.

**Fallback Flooding** — When all pre-computed routes fail (link degradation, killed nodes), System 5 falls back to scoped flooding within the source cluster. This is much cheaper than full network flooding because it's limited to the local geographic area.

## The Killer Argument: Hop Limits Become Irrelevant

In flooding, every hop multiplies transmissions across the **entire** network: `TX = n * hops`. The hop limit (default 3 in Meshtastic) is a survival mechanism — without it, the network drowns.

In System 5, every hop costs **exactly 1 transmission**: `TX = hops`. The simulation confirms ~1.0 TX/hop across all scenarios, regardless of network size. This means:

- **No artificial hop limit needed** — 20 or 50 hops cost less than flooding costs for 1
- **Unlimited range** — limited only by node density, not protocol constraints
- **Preset freedom** — SHORT_FAST with more hops works as well as LONG_SLOW with fewer
- **Battery independence** — only forwarding nodes transmit, the rest sleep

## Simulation Results

The `simulator/` directory contains a Python simulation comparing System 5 against naive flooding across 8 scenarios:

### Normal Conditions

| Scenario | Nodes | Flooding TX | System 5 TX | S5 Delivery | BW Saved |
|----------|------:|------------:|------------:|------------:|---------:|
| Small Local (1km) | 20 | 31,525 | 112 | 100% | 99.6% |
| Medium City (5km) | 100 | 330,181 | 196 | 100% | 99.9% |
| Large Regional (20km) | 500 | 1,535,089 | 497 | 100% | 99.97% |
| Dense Urban (3km) | 200 | 2,694,702 | 136 | 100% | 100.0% |

### Stress Tests

| Scenario | Flooding TX | System 5 TX | S5 Delivery | BW Saved |
|----------|------------:|------------:|------------:|---------:|
| 30% Degraded Links | 330,181 | 4,701 | 100% | 98.6% |
| 50% Degraded Links | 330,181 | 16,055 | 73% | 95.1% |
| 20% Nodes Killed | 215,708 | 2,853 | 85% | 98.7% |
| Combined (30% + 10%) | 271,210 | 3,427 | 79% | 98.7% |

Under normal conditions, System 5 delivers **100% of messages** using **99.6-99.97% less bandwidth**. Under extreme stress (50% degraded links), delivery drops to 73% — but still uses 95% less bandwidth. The fallback mechanism catches packets that pre-computed routes miss.

## Interactive Presentation

Open `index.html` in a browser. No build step, no dependencies — pure HTML, CSS, and Canvas.

The presentation includes:
- **Live algorithm visualizations** for all five routing approaches
- **Simulation results** with interactive charts (loaded from `simulator/results.json`)
- **Step-by-step formation animation** showing how the network self-organizes
- **Three scale scenarios** — local (12 nodes), continental (2,400 nodes), global (50,000 nodes)
- **Interactive resilience testing** — click nodes and links to kill them, watch the network adapt. Toggle MQTT bridge failures, GPS outages, and cascade failures.
- **QoS priority gate** visualization with real-time NHS gauge

A separate `summary.html` provides the executive summary.

## Running the Simulator

```bash
cd simulator
python run.py                    # run all 5 benchmark scenarios
python run.py --scenario 2       # run only scenario 2 (Medium City)
python run.py --visualize        # ASCII network topology
python run.py -s 3 -v            # scenario 3 with visualization
python run.py --output out.json  # custom output file
```

Results are saved to `simulator/results.json`.

### Simulator Architecture

```
simulator/
  run.py          — CLI entry point
  meshsim.py      — Network simulation engine (nodes, links, clusters, routes)
  routing.py      — FloodingRouter and System5Router implementations
  lora_model.py   — EU 868MHz LoRa physical layer (path loss, RSSI, SNR, time-on-air)
  geohash.py      — Geographic clustering via geohash encoding
  benchmark.py    — Scenario definitions and comparative benchmarking
```

The LoRa model uses a log-distance path loss model (exponent 2.8 for urban environments) with a sigmoid packet success rate centered at -120 dBm receiver sensitivity.

## Architecture Origins

System 5 doesn't invent new concepts — it combines proven ones:

| Source | Concept | Used As |
|--------|---------|---------|
| Internet (OSPF) | Area-based hierarchy | Geo-clusters with border nodes |
| Freifunk (B.A.T.M.A.N.) | OGM counting | Link quality metric |
| Data Centers (ECMP) | Weighted multi-path | Proportional load distribution |
| Network Theory | Back-pressure | Congestion avoidance |
| Ant Colony Optimization | Pheromone decay | Self-optimizing route weights |
| DNS | Hierarchical cache | Scoped node discovery |

## Project Status

This is a research project and proof-of-concept simulator. There is no Meshtastic firmware implementation yet. The goal is to validate the routing approach through simulation before proposing changes to the Meshtastic protocol.

### Roadmap

- [x] Algorithm design and mathematical analysis
- [x] Interactive presentation with live visualizations
- [x] Python simulation framework
- [x] Comparative benchmarks (Flooding vs System 5)
- [ ] Firmware prototype (ESP32 / Meshtastic fork)
- [ ] Field testing with real LoRa hardware
- [ ] RFC / proposal to Meshtastic community

## License

MIT — see [LICENSE](LICENSE)

## Author

[Clemens Simon](https://github.com/ClemensSimon)
