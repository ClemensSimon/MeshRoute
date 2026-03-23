# Draft Response to @h3lix1 and @shalberd — Broadcast Routing in System 5

> **STATUS: DRAFT — Zur Überprüfung vor dem Posten**

---

Hey @h3lix1, @shalberd,

Thank you for the clarity on the 98% broadcast reality — that's the critical piece I needed. You're right that System 5 as demonstrated primarily optimizes unicast. Let me lay out how the architecture can handle broadcast traffic, and where @h3lix1's Bloom Filter idea fits in.

## The Broadcast Problem, Precisely

In a 235-node Bay Area mesh with managed flooding:
- 1 position packet → **235+ TX** (every node rebroadcasts)
- 100 nodes sending position every 15 min → **~94,000 TX/hour**
- With half-duplex collisions, most of those TX are wasted

The question isn't "should every node hear every position?" — it's "can we deliver the same broadcast reach with fewer TX?"

## Three Approaches That Could Work Together

### 1. Cluster-Scoped Broadcast (System 5 native)

System 5 already has geo-clusters. Use them for broadcast scope:

- **Intra-cluster**: Flood normally within your cluster (small, ~10-30 nodes, manageable)
- **Inter-cluster**: Only **border nodes** relay to adjacent clusters — 1 TX per cluster boundary instead of N
- **Result**: Broadcast cost goes from O(n) to O(clusters × cluster_size), roughly O(√n)

For Bay Area (7 mountain + 35 hill + 193 valley):
- Valley nodes broadcast only within their cluster (~15-20 nodes each)
- Hill/mountain border nodes relay between clusters
- **Estimated: ~15-25 TX per broadcast instead of ~235**

### 2. Bloom Filter Hybrid (@h3lix1's RBF from #8592)

Your Bloom Filter approach and System 5 are complementary:

- **System 5** knows the cluster topology and border nodes → **where** to route
- **Bloom Filters** track which nodes have already seen a packet → **who** to skip

Combined: Border nodes carry a Bloom filter in the broadcast packet. When relaying to the next cluster, nodes already in the filter don't rebroadcast. This handles the overlap zones where clusters share radio range.

The 11-35 byte filter cost is negligible vs. saving dozens of redundant TX at cluster boundaries.

### 3. @fifieldt's Interior/Exterior Split — Already Built

@shalberd, great catch. System 5's geo-clustering **is** the interior/exterior split that @fifieldt described:

- **Interior** = intra-cluster routing (flood within cluster, small scope)
- **Exterior** = inter-cluster routing via border nodes (directed, 1 TX per hop)

The only missing piece is applying this to **broadcast** traffic, not just unicast. The cluster infrastructure is already there.

## What I'll Build Next

1. **Cluster-scoped broadcast mode** in the simulator — measure TX savings vs. delivery rate
2. **Bloom filter integration** at cluster boundaries for overlap deduplication
3. **Broadcast scenario benchmarks** — 100 nodes all sending position packets, System 5 cluster-broadcast vs. managed flooding

## Honest Limitations

- **Latency**: Cluster-scoped broadcast adds relay hops → slightly higher latency than direct flooding for nearby nodes
- **Consistency**: Not all nodes will have the same view at the same time (but that's already true with hop limits)
- **OGM overhead**: Neighbor discovery still needs some flooding — can't route what you haven't discovered

Would this address your use case? Specifically: if position/telemetry packets reached all nodes within ~5-10 seconds instead of ~1-3 seconds, but used 90% less airtime — would that tradeoff work for Bay Mesh?

— Clemens
