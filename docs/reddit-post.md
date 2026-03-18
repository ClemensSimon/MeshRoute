# r/meshtastic Post

**Title:** I built a routing algorithm that reduces Meshtastic transmissions by 97% — live simulator + ESP32 firmware inside

---

I've been working on an alternative routing approach for Meshtastic called **System 5** (geo-clustered multi-path routing). Instead of every node rebroadcasting every message (managed flooding), messages follow a **pre-computed path** — 1 TX per hop instead of N.

## Results (22 benchmark scenarios)

- **Medium City (100 nodes)**: 201,920 TX (flooding) → 5,678 TX (System 5) = **97% less**
- **Festival (100 mobile nodes)**: 912,953 → 107 TX = **99.99% less**
- **Building Emergency (200 nodes)**: 3,651,559 → 386 TX = **99.99% less**
- **1500 Nodes**: Flooding delivers 36%, System 5 delivers **51%** — it actually works BETTER at scale

## Try it yourself

**[Live Simulator](https://clemenssimon.github.io/MeshRoute/simulator.html)** — Click Step to watch hop-by-hop how flooding lights up the entire network while System 5 sends one packet along a direct path. No install needed, runs in your browser.

## How it works

1. Nodes self-organize into **GPS-based clusters** (geohash)
2. **Border nodes** connect clusters (like OSPF area border routers)
3. Each node has a **routing table** with multiple paths per destination
4. Messages route **directly** along the best path — no broadcasting
5. If the route fails → **scoped fallback** flooding (only SRC+DST clusters, not the whole network)

## Backward compatible

S5 nodes coexist with legacy Meshtastic nodes. Between S5 nodes: directed routing. To/from legacy nodes: normal managed flooding. The simulator has mixed-mode scenarios (10%–90% S5) showing the transition.

## ESP32 firmware

Working standalone firmware for **Heltec V3**, **T-Beam**, and **RAK4631**. No Meshtastic fork needed. Neighbor discovery via OGM, directed routing, GPS + RSSI triangulation for boards without GPS.

## Honest about weaknesses

- Mountain/partition scenarios: both flooding and S5 struggle
- 50% degraded links: flooding delivers 100%, S5 delivers 73% (trade-off: 91% fewer TX)
- S5 needs GPS (or triangulation from 3+ neighbors) for clustering

**Repo:** https://github.com/ClemensSimon/MeshRoute
**Simulator:** https://clemenssimon.github.io/MeshRoute/simulator.html

Would love feedback — what real-world scenarios would break this? Anyone want to test on hardware?
