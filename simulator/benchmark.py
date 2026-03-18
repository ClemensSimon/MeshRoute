"""
Benchmark runner for MeshRoute simulator.
Runs both Flooding and System 5 routers on identical scenarios and compares results.
"""

import json
import time
import random
from collections import defaultdict

from meshsim import MeshNetwork, Packet
from routing import FloodingRouter, System5Router


class ScenarioConfig:
    """Configuration for a benchmark scenario."""

    def __init__(self, name, n_nodes, area_size, lora_range=2000,
                 n_messages=100, link_degradation=0.0, node_kill_fraction=0.0,
                 geohash_prefix=4):
        self.name = name
        self.n_nodes = n_nodes
        self.area_size = area_size
        self.lora_range = lora_range
        self.n_messages = n_messages
        self.link_degradation = link_degradation
        self.node_kill_fraction = node_kill_fraction
        self.geohash_prefix = geohash_prefix


# Standard scenarios
SCENARIOS = [
    # --- Scale tests ---
    ScenarioConfig(
        name="Small Local Mesh",
        n_nodes=20,
        area_size=1000,
        lora_range=800,
        n_messages=100,
        geohash_prefix=6,
    ),
    ScenarioConfig(
        name="Medium City Mesh",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=100,
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Large Regional Mesh",
        n_nodes=500,
        area_size=20000,
        lora_range=3000,
        n_messages=100,
        geohash_prefix=4,
    ),
    # --- Stress tests ---
    ScenarioConfig(
        name="Stress Test (30% degraded links)",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=100,
        link_degradation=0.3,
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Stress Test (50% degraded links)",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=100,
        link_degradation=0.5,
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Node Failure (20% killed)",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=100,
        node_kill_fraction=0.2,
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Combined Stress (30% links + 10% nodes)",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=100,
        link_degradation=0.3,
        node_kill_fraction=0.1,
        geohash_prefix=5,
    ),
    # --- Dense urban ---
    ScenarioConfig(
        name="Dense Urban (high connectivity)",
        n_nodes=200,
        area_size=3000,
        lora_range=2000,
        n_messages=100,
        geohash_prefix=5,
    ),
    # --- Large scale / long hop chains ---
    ScenarioConfig(
        name="Large Scale (1000 nodes, 40km)",
        n_nodes=1000,
        area_size=40000,
        lora_range=4000,
        n_messages=100,
        geohash_prefix=3,
    ),
    ScenarioConfig(
        name="Metro Scale (1500 nodes, 50km)",
        n_nodes=1500,
        area_size=50000,
        lora_range=4000,
        n_messages=100,
        geohash_prefix=3,
    ),
]


class BenchmarkResult:
    """Results from running a single router on a scenario."""

    def __init__(self, router_name):
        self.router_name = router_name
        self.messages_sent = 0
        self.messages_delivered = 0
        self.total_tx = 0
        self.total_hops = 0
        self.max_node_load = 0
        self.node_tx_counts = defaultdict(int)
        self.delivery_rate = 0.0
        self.tx_per_delivered = 0.0
        self.avg_hops = 0.0
        self.energy_score = 0.0
        # Extended stats
        self.qos_stats = {}  # priority -> {sent, delivered}
        self.fallback_used = 0
        self.route_switches = 0

    def compute_derived(self):
        """Compute derived metrics after all messages are processed."""
        self.delivery_rate = (
            (self.messages_delivered / self.messages_sent * 100)
            if self.messages_sent > 0
            else 0.0
        )
        self.tx_per_delivered = (
            (self.total_tx / self.messages_delivered)
            if self.messages_delivered > 0
            else float("inf")
        )
        self.avg_hops = (
            (self.total_hops / self.messages_delivered)
            if self.messages_delivered > 0
            else 0.0
        )
        self.energy_score = self.tx_per_delivered
        self.max_node_load = (
            max(self.node_tx_counts.values()) if self.node_tx_counts else 0
        )

    def _load_distribution(self):
        """Compute load distribution buckets for visualization."""
        if not self.node_tx_counts:
            return []
        counts = sorted(self.node_tx_counts.values())
        max_load = max(counts) if counts else 1
        # Create 10 buckets
        buckets = [0] * 10
        for c in counts:
            bucket = min(int(c / max(max_load, 1) * 9), 9)
            buckets[bucket] += 1
        return buckets

    def to_dict(self):
        result = {
            "router": self.router_name,
            "messages_sent": self.messages_sent,
            "messages_delivered": self.messages_delivered,
            "delivery_rate": round(self.delivery_rate, 1),
            "total_tx": self.total_tx,
            "tx_per_delivered": round(self.tx_per_delivered, 1),
            "avg_hops": round(self.avg_hops, 1),
            "energy_score": round(self.energy_score, 1),
            "max_node_load": self.max_node_load,
            "load_distribution": self._load_distribution(),
        }
        if self.qos_stats:
            result["qos_breakdown"] = {}
            for priority in range(8):
                ps = self.qos_stats.get(priority, {"sent": 0, "delivered": 0})
                if ps["sent"] > 0:
                    result["qos_breakdown"][str(priority)] = {
                        "sent": ps["sent"],
                        "delivered": ps["delivered"],
                        "rate": round(ps["delivered"] / ps["sent"] * 100, 1),
                    }
        if self.fallback_used > 0:
            result["fallback_used"] = self.fallback_used
        if self.route_switches > 0:
            result["route_switches"] = self.route_switches
        return result


def build_network(config, seed=42):
    """Build a mesh network from scenario config.

    Args:
        config: ScenarioConfig
        seed: Random seed

    Returns:
        Configured MeshNetwork
    """
    net = MeshNetwork(seed=seed)
    net.build_topology(config.n_nodes, config.area_size, config.lora_range)
    net.compute_geohash_clusters(config.geohash_prefix)
    net.elect_border_nodes()
    net.run_ogm_round()
    net.compute_routes()
    net.compute_nhs()

    # Apply degradation if configured
    if config.link_degradation > 0:
        net.degrade_links(config.link_degradation)
        # Recompute routes after degradation
        net.compute_routes()
        net.compute_nhs()

    # Kill nodes if configured
    if config.node_kill_fraction > 0:
        n_kill = int(len(net.nodes) * config.node_kill_fraction)
        kill_ids = net.rng.sample(list(net.nodes.keys()), n_kill)
        for nid in kill_ids:
            net.kill_node(nid)
        # Recompute routes after node failures
        net.compute_routes()
        net.compute_nhs()

    return net


def generate_messages(network, n_messages, seed=42):
    """Generate random source-destination message pairs.

    Only picks alive nodes with neighbors.

    Args:
        network: MeshNetwork
        n_messages: Number of messages to generate
        seed: Random seed

    Returns:
        List of (src_id, dst_id, priority) tuples
    """
    rng = random.Random(seed)
    alive_nodes = [
        nid for nid, node in network.nodes.items()
        if node.battery > 0 and len(node.neighbors) > 0
    ]

    if len(alive_nodes) < 2:
        return []

    messages = []
    for _ in range(n_messages):
        src = rng.choice(alive_nodes)
        dst = rng.choice(alive_nodes)
        while dst == src:
            dst = rng.choice(alive_nodes)
        priority = rng.randint(0, 7)
        messages.append((src, dst, priority))

    return messages


def run_router(router, network, messages):
    """Run a router on a set of messages and collect results.

    Args:
        router: FloodingRouter or System5Router instance
        network: MeshNetwork
        messages: List of (src, dst, priority) tuples

    Returns:
        BenchmarkResult
    """
    result = BenchmarkResult(router.__class__.__name__)
    Packet._next_id = 0  # reset packet IDs

    # Reset node stats
    for node in network.nodes.values():
        node.packets_sent = 0
        node.packets_forwarded = 0
        node.packets_received = 0
        node.queue.clear()

    # Reset router stats if System5
    if hasattr(router, 'qos_stats'):
        router.qos_stats.clear()
        router.fallback_used = 0
        router.route_switches = 0

    for i, (src, dst, priority) in enumerate(messages):
        packet = Packet(src, dst, priority=priority)
        packet.created_at = i
        network.tick = i

        stats = router.route(network, packet)

        result.messages_sent += 1
        if stats.delivered:
            result.messages_delivered += 1
            result.total_hops += stats.hops
        result.total_tx += stats.total_tx

        for nid, count in stats.node_tx_counts.items():
            result.node_tx_counts[nid] += count

    result.compute_derived()

    # Copy extended stats from System5Router
    if hasattr(router, 'qos_stats'):
        result.qos_stats = {k: dict(v) for k, v in router.qos_stats.items()}
        result.fallback_used = router.fallback_used
        result.route_switches = router.route_switches

    return result


def run_scenario(config, scenario_num, verbose=True):
    """Run a complete scenario with both routers.

    Args:
        config: ScenarioConfig
        scenario_num: Scenario number for display
        verbose: Whether to print progress

    Returns:
        Dict with scenario results
    """
    if verbose:
        print(f"\nScenario {scenario_num}: {config.name} "
              f"({config.n_nodes} nodes, {config.area_size/1000:.0f}km)")

    # Build network
    if verbose:
        print(f"  Building topology...", end=" ", flush=True)
    net = build_network(config, seed=42)
    net_stats = net.stats_summary()
    if verbose:
        print(f"{net_stats['nodes']} nodes, {net_stats['links']} links")

    if verbose:
        print(f"  Computing clusters...", end=" ", flush=True)
        print(f"{net_stats['clusters']} cluster(s)")

    if verbose:
        print(f"  Running OGM round... link qualities computed")
        print(f"  Computing routes... avg {net_stats['avg_routes_per_dest']} "
              f"routes per destination")

    # Generate messages (same for both routers)
    messages = generate_messages(net, config.n_messages, seed=42)
    if not messages:
        if verbose:
            print("  WARNING: Not enough alive nodes to generate messages!")
        return None

    # --- Run Flooding ---
    if verbose:
        print(f"\n  --- Flooding ---")

    # Build fresh network for flooding (same topology)
    net_flood = build_network(config, seed=42)
    flooding = FloodingRouter(seed=42)
    flood_result = run_router(flooding, net_flood, messages)

    if verbose:
        print(f"  Messages: {flood_result.messages_sent} | "
              f"Delivered: {flood_result.messages_delivered} | "
              f"Rate: {flood_result.delivery_rate:.1f}%")
        print(f"  Total TX: {flood_result.total_tx} | "
              f"TX/delivered: {flood_result.tx_per_delivered:.1f}")
        print(f"  Avg hops: {flood_result.avg_hops:.1f} | "
              f"Max node load: {flood_result.max_node_load}")

    # --- Run System 5 ---
    if verbose:
        print(f"\n  --- System 5 ---")

    # Build fresh network for System 5
    net_s5 = build_network(config, seed=42)
    system5 = System5Router(seed=42)
    s5_result = run_router(system5, net_s5, messages)

    if verbose:
        print(f"  Messages: {s5_result.messages_sent} | "
              f"Delivered: {s5_result.messages_delivered} | "
              f"Rate: {s5_result.delivery_rate:.1f}%")
        print(f"  Total TX: {s5_result.total_tx} | "
              f"TX/delivered: {s5_result.tx_per_delivered:.1f}")
        print(f"  Avg hops: {s5_result.avg_hops:.1f} | "
              f"Max node load: {s5_result.max_node_load}")

    # --- Comparison ---
    if verbose and flood_result.total_tx > 0 and s5_result.total_tx > 0:
        bw_savings = (1 - s5_result.total_tx / flood_result.total_tx) * 100
        load_reduction = (
            (1 - s5_result.max_node_load / flood_result.max_node_load) * 100
            if flood_result.max_node_load > 0
            else 0
        )
        print(f"\n  -> System 5 uses {bw_savings:.1f}% less bandwidth")
        print(f"  -> Max node load reduced by {load_reduction:.1f}%")

    # Compute category
    if config.link_degradation > 0 or config.node_kill_fraction > 0:
        category = "stress"
    elif config.n_nodes >= 200:
        category = "dense"
    else:
        category = "scale"

    # Bandwidth savings
    bw_savings = 0.0
    if flood_result.total_tx > 0:
        bw_savings = round((1 - s5_result.total_tx / flood_result.total_tx) * 100, 2)

    return {
        "scenario": scenario_num,
        "name": config.name,
        "category": category,
        "config": {
            "n_nodes": config.n_nodes,
            "area_size": config.area_size,
            "lora_range": config.lora_range,
            "n_messages": config.n_messages,
            "link_degradation": config.link_degradation,
            "node_kill_fraction": config.node_kill_fraction,
        },
        "network": net_stats,
        "flooding": flood_result.to_dict(),
        "system5": s5_result.to_dict(),
        "comparison": {
            "bw_savings_pct": bw_savings,
            "load_reduction_pct": round(
                (1 - s5_result.max_node_load / flood_result.max_node_load) * 100, 1
            ) if flood_result.max_node_load > 0 else 0.0,
            "tx_ratio": round(
                s5_result.total_tx / flood_result.total_tx, 4
            ) if flood_result.total_tx > 0 else 0.0,
        },
    }


def run_all_scenarios(verbose=True):
    """Run all benchmark scenarios.

    Args:
        verbose: Whether to print progress

    Returns:
        List of scenario result dicts
    """
    if verbose:
        print("=" * 50)
        print("  MeshRoute Simulator v0.1")
        print("=" * 50)

    results = []
    for i, config in enumerate(SCENARIOS, 1):
        result = run_scenario(config, i, verbose=verbose)
        if result:
            results.append(result)

    return results


def print_summary_table(results):
    """Print a summary comparison table."""
    print("\n" + "=" * 80)
    print("  SUMMARY TABLE")
    print("=" * 80)

    header = (
        f"{'Scenario':<35} {'Router':<10} {'Del%':>6} "
        f"{'TotalTX':>8} {'TX/Del':>7} {'Hops':>5} {'MaxLoad':>8}"
    )
    print(header)
    print("-" * 80)

    for r in results:
        for router_key, label in [("flooding", "Flood"), ("system5", "Sys5")]:
            d = r[router_key]
            name = r["name"][:33]
            print(
                f"  {name:<33} {label:<10} {d['delivery_rate']:>5.1f}% "
                f"{d['total_tx']:>8} {d['tx_per_delivered']:>7.1f} "
                f"{d['avg_hops']:>5.1f} {d['max_node_load']:>8}"
            )
        print()


def save_results(results, filename="results.json"):
    """Save results to JSON file.

    Args:
        results: List of scenario result dicts
        filename: Output filename
    """
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {filename}")
