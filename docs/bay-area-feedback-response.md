# Response to @h3lix1 — System 5 vs Bay Area Reality

Hey h3lix1,

Thank you for taking the time to write such detailed, practical feedback. Your questions about asynchronous paths, half-duplex blocking, the SUNL collision problem, and nRF52 memory constraints were exactly the kind of real-world stress test that simulations alone can't provide.

**Your feedback directly led to 5 new features being implemented and deployed.** Here's what changed, what we built, and what the numbers look like now.

---

## What We Built Because of Your Feedback

### 1. Half-Duplex Radio Model (your #1 concern)

You wrote: *"while the hill and rooftop nodes send, the mountaintop nodes are blocked from sending. This must be in the simulation."*

**Done.** We added a `HalfDuplexRadio` state machine to the simulator. Every node now tracks its radio state (IDLE / TX / RX). When a node is receiving a packet, it **cannot transmit** — exactly like real LoRa hardware. All neighbors within range are marked as "in RX" when any node transmits.

### 2. Bay Area 3-Tier Topology (your SUNL scenario)

You described the Bay Area structure: mountaintop routers hearing 10+ rooftop nodes simultaneously, with 5% utilization exploding to 50%. **We built exactly this:**

- **7 mountaintop nodes** (2000-4000 ft, free-space propagation, 45km range, SF12, solar)
- **35 hill/rooftop nodes** (500-1600 ft, suburban terrain, 10km range, SF10)
- **193 valley/indoor nodes** (sea level, urban/indoor terrain, 0.75-2.5km range, SF7)
- Asymmetric links: mountaintop→valley quality ~1.0, valley→mountaintop quality ~0
- Collision capture effect (≥6dB wins)

**Try it live:** [clemenssimon.github.io/MeshRoute/simulator.html](https://clemenssimon.github.io/MeshRoute/simulator.html) — select "Bay Area Mesh" from the dropdown.

### 3. Node Silencing (inspired by your collision cascade description)

Your observation that *"all clients repeating packets at high elevations cause a mess"* led to a new feature: **Selective Node Silencing**.

The network identifies redundant nodes (those whose neighbors are all reachable via other paths) and **mutes them**. Silenced nodes still listen — they receive OGMs, accept direct messages, the network knows they exist — but they **do not rebroadcast**. This directly reduces the collision load at mountaintops.

Key design decisions:
- **Battery-fair rotation**: silenced nodes rotate every 10 minutes so batteries drain evenly
- **Low-battery priority**: nodes with 20% battery get silenced first; solar nodes stay active
- **Critical bridge protection**: border nodes with few alternatives are never silenced
- **Minimum 2 active per cluster**: prevents accidentally silencing an entire cluster
- **Self-reactivation**: if a silenced node detects a neighbor went down (no OGM for 90s), it wakes up automatically

### 4. Sequence Numbers in Wire Protocol (your out-of-order concern)

You asked: *"how does it work with asynchronous paths? Messages sent A B C can be received C B A."*

**Implemented.** The wire protocol header now includes a `uint16_t seq` field (2 bytes) — a per-(source, destination) sequence counter. The sender increments it for each message to a given destination. The receiver can detect gaps: "I got seq 5 and seq 7 — seq 6 is missing."

This doesn't add retransmission (too expensive over LoRa) but gives the app layer the information it needs to show the user what's missing. Zero extra TX cost — just 2 bytes added to the existing header (24 bytes total, was 22).

### 5. Emergency Re-Route (reducing fallback flooding)

You pointed out that load balancing with only one working path creates single points of failure. We added an **emergency re-route**: when all 5 cached routes fail, System 5 now computes a fresh BFS path on the fly, **excluding all nodes that already failed**. Only if this emergency path also fails does it trigger corridor flooding. This reduced fallback floods significantly.

---

## The Numbers — Before and After Your Feedback

### Bay Area Topology Results (235 nodes, 50km, half-duplex)

**Idealized (no half-duplex) — what most simulators show:**

| Router | Delivery | Total TX | TX/Delivered |
|--------|:--------:|:--------:|:------------:|
| Managed Flooding (7 hop) | 87.5% | 908,785 | 5,193 |
| **System 5** | **80.5%** | **47,094** | **293** |

System 5 saves **94.8%** of transmissions. Both deliver well.

**Realistic (with half-duplex) — what actually happens at SUNL:**

| Router | Delivery | Total TX | TX/Delivered |
|--------|:--------:|:--------:|:------------:|
| Managed Flooding (7 hop) | **6.0%** | 6,752 | 563 |
| **System 5** | **77.5%** | 540,780 | 3,489 |
| **System 5 + Silencing** | **74.5%** | **267,927** | **1,861** |

**What the numbers mean:**

1. **Half-duplex kills flooding.** 87.5% → 6.0% delivery. This is your SUNL problem in numbers: the mountaintop nodes are stuck in RX from 10+ simultaneous rebroadcasts and never get a TX window. Messages die at hop 1.

2. **System 5 survives half-duplex.** 80.5% → 77.5% delivery. Directed routing sends one packet along the computed path. The mountaintop receives it, forwards to the next hop, done. No 14 simultaneous rebroadcasts competing for airtime.

3. **Node Silencing halves the TX cost.** 540K → 268K transmissions with only 3% less delivery. 128 of 193 valley nodes are muted (they were just adding collision noise at the mountaintops). All 7 mountain nodes stay active. The 35 hill/rooftop nodes mostly stay active as bridges.

### Under Stress (15% node failure + 20% link degradation)

| Router | Delivery | Total TX |
|--------|:--------:|:--------:|
| Managed Flooding | **4.0%** | 6,417 |
| System 5 | **52.0%** | 301,757 |
| **System 5 + Silencing** | **51.0%** | **155,393** |

System 5 delivers **13× more messages** than managed flooding under stress. With silencing, it does so at half the TX cost.

---

## Your Original Questions — Updated Answers

### 1. Asynchronous paths / out-of-order messaging

**Now implemented:** 2-byte sequence number per (src, dst) pair in every packet header. Zero extra airtime. The receiver sees `seq 3, 5, 4` and knows it got everything but out of order, or `seq 3, 5` and knows seq 4 is missing. The app layer can reorder or flag gaps.

System 5 operates at Layer 3 (routing). We deliberately don't add TCP-style retransmission at the mesh layer — each retransmit costs seconds of LoRa airtime. But with sequence numbers, the app can make informed decisions.

### 2. Asymmetric return paths (3 hops up, 1 hop down)

**Already works correctly.** Routes are computed per-direction with independent link quality values. The path TO a mountaintop (3 hops through hill nodes, quality 0.2 per hop) is completely independent from the path FROM the mountaintop (1 hop direct, quality 0.95). System 5 naturally selects the best path in each direction.

### 3. SUNL / mountaintop collision cascade

**Three layers of defense now:**

1. **Directed routing** — mountaintop processes 1 targeted packet instead of 14 rebroadcasts
2. **Node silencing** — 66% of valley nodes muted, reducing collision sources at the mountaintop
3. **Backpressure** — overloaded mountaintop nodes automatically shed traffic to alternative routes

### 4. Half-duplex simulation

**Fully implemented.** `HalfDuplexRadio` class with per-node state machine (IDLE/TX/RX). Tracks `tx_blocked_count` and `rx_blocked_count`. All 6 routers in the simulator respect half-duplex constraints for fair comparison.

### 5. Missing message detection

**Partially solved.** Sequence numbers provide gap detection. Full ACKs remain too expensive for LoRa. We're considering a Bloom filter digest approach: nodes periodically broadcast a compact summary of received packet IDs so neighbors can check delivery without individual ACKs.

### 6. Load balancing / single point of failure

**Improved.** Emergency re-route now computes a fresh BFS path excluding failed nodes before triggering corridor flooding. 5 cached routes + 1 emergency BFS + scoped corridor flood = 7 layers of fallback.

### 7. nRF52 memory for 1000-10000 nodes

**Redesigned.** Sequence counters now use a neighbor-indexed array + LRU cache (128 bytes) instead of a flat array indexed by node ID (which would break with non-compact IDs common in Meshtastic). Total node state: ~1.6 KB. For nRF52 solar routers with tight memory:

```
Default:  S5_MAX_ROUTES=5, S5_MAX_PATH_LEN=15 → ~30 KB routing state
Reduced:  S5_MAX_ROUTES=2, S5_MAX_PATH_LEN=8  → ~15 KB routing state
```

Both fit in nRF52840's available 64 KB after BLE/LoRa stacks.

---

## What's Still Missing (honest gaps)

| Feature | Status | Plan |
|---------|--------|------|
| Field testing with real hardware | Not started | Need 3+ boards (Heltec V3 / T-Beam / RAK4631) |
| Listen-before-talk (LBT) | Not modeled | Would improve collision avoidance further |
| End-to-end ACKs | Won't implement | Too expensive for LoRa; sequence numbers + Bloom filters instead |
| Elevation-aware routing preference | Not yet | Mountaintops could be weighted higher as relay candidates |
| Proactive path probing | Not yet | Periodic probe on secondary routes for instant failover |

---

## Try It Yourself

- **[Live Simulator](https://clemenssimon.github.io/MeshRoute/simulator.html)** — Bay Area Mesh scenario with half-duplex, or any of the 26 other scenarios
- **[Full Presentation](https://clemenssimon.github.io/MeshRoute/)** — all algorithms compared, live animations, 26-scenario results with category filters
- **[How System 5 Works](https://clemenssimon.github.io/MeshRoute/how-it-works.html)** — step-by-step technical deep dive from boot to delivery
- **[Source Code](https://github.com/ClemensSimon/MeshRoute)** — MIT license, everything open

Your feedback genuinely made this better. The half-duplex model alone was worth the entire conversation — it revealed that the real-world problem isn't routing efficiency but **radio physics at elevated nodes**, and that's something neither Meshtastic's current approach nor most mesh simulators account for.

— Clemens
