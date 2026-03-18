"""
Benchmark runner for MeshRoute simulator.
Runs both Flooding and System 5 routers on identical scenarios and compares results.
Supports parallel execution via multiprocessing (auto-detects CPU cores).
"""

import json
import time
import random
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from meshsim import MeshNetwork, Packet
from routing import NaiveFloodingRouter, ManagedFloodingRouter, NextHopRouter, System5Router

# Router registry for multiprocessing (must be picklable by name)
ROUTER_REGISTRY = [
    ("naive_flooding", "Naive Flood", NaiveFloodingRouter),
    ("managed_flooding", "Managed Flood", ManagedFloodingRouter),
    ("next_hop", "Next-Hop", NextHopRouter),
    ("system5", "System 5", System5Router),
]


class ScenarioConfig:
    """Configuration for a benchmark scenario."""

    def __init__(self, name, n_nodes, area_size, lora_range=2000,
                 n_messages=100, link_degradation=0.0, node_kill_fraction=0.0,
                 geohash_prefix=4, terrain="urban", asymmetry=0.0,
                 mobile_fraction=0.0, placement="random",
                 enable_duty_cycle=False, enable_collisions=False):
        self.name = name
        self.n_nodes = n_nodes
        self.area_size = area_size
        self.lora_range = lora_range
        self.n_messages = n_messages
        self.link_degradation = link_degradation
        self.node_kill_fraction = node_kill_fraction
        self.geohash_prefix = geohash_prefix
        self.terrain = terrain
        self.asymmetry = asymmetry
        self.mobile_fraction = mobile_fraction
        self.placement = placement
        self.enable_duty_cycle = enable_duty_cycle
        self.enable_collisions = enable_collisions


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
    # --- Realistic environment scenarios ---
    ScenarioConfig(
        name="Rural Long Range (SF12)",
        n_nodes=50,
        area_size=15000,
        lora_range=5000,
        n_messages=100,
        terrain="rural",
        asymmetry=0.15,
        geohash_prefix=4,
    ),
    ScenarioConfig(
        name="Hiking Trail (linear)",
        n_nodes=40,
        area_size=8000,
        lora_range=2000,
        n_messages=100,
        terrain="rural",
        placement="linear",
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Festival/Event (dense + mobile)",
        n_nodes=150,
        area_size=2000,
        lora_range=1500,
        n_messages=100,
        terrain="suburban",
        mobile_fraction=0.6,
        placement="clustered",
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Disaster Relief (asymmetric + node loss)",
        n_nodes=80,
        area_size=10000,
        lora_range=3000,
        n_messages=100,
        terrain="suburban",
        asymmetry=0.3,
        node_kill_fraction=0.25,
        mobile_fraction=0.3,
        geohash_prefix=4,
    ),
    ScenarioConfig(
        name="Indoor-Outdoor Mix (dense urban)",
        n_nodes=100,
        area_size=2000,
        lora_range=1000,
        n_messages=100,
        terrain="dense_urban",
        asymmetry=0.2,
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Duty Cycle Stress (100 nodes, 1% enforced)",
        n_nodes=100,
        area_size=5000,
        lora_range=2000,
        n_messages=200,
        terrain="urban",
        enable_duty_cycle=True,
        geohash_prefix=5,
    ),
    # --- Extended realistic scenarios ---
    ScenarioConfig(
        name="Mountain Valley (poor propagation)",
        n_nodes=60,
        area_size=12000,
        lora_range=2000,
        n_messages=100,
        terrain="dense_urban",  # high path loss simulates mountain shadowing
        asymmetry=0.35,         # valleys create very asymmetric links
        geohash_prefix=4,
    ),
    ScenarioConfig(
        name="Maritime / Coastal (line of sight)",
        n_nodes=30,
        area_size=25000,
        lora_range=8000,        # free space = huge range over water
        n_messages=100,
        terrain="free_space",
        asymmetry=0.05,         # nearly perfect symmetry over water
        geohash_prefix=3,
    ),
    ScenarioConfig(
        name="Building Emergency (high density, high load)",
        n_nodes=200,
        area_size=500,          # single building
        lora_range=300,
        n_messages=300,         # SOS messages, high load
        terrain="indoor",
        mobile_fraction=0.8,    # people moving
        placement="clustered",  # clustered per floor
        geohash_prefix=6,
    ),
    ScenarioConfig(
        name="Highway Convoy (fast linear mobile)",
        n_nodes=50,
        area_size=10000,
        lora_range=3000,
        n_messages=100,
        terrain="rural",
        mobile_fraction=0.9,    # almost all moving
        placement="linear",
        geohash_prefix=4,
    ),
    ScenarioConfig(
        name="Community Mesh (stable, low traffic)",
        n_nodes=80,
        area_size=8000,
        lora_range=2500,
        n_messages=50,          # low traffic community
        terrain="suburban",
        asymmetry=0.1,
        placement="clustered",  # neighborhoods
        geohash_prefix=5,
    ),
    ScenarioConfig(
        name="Partition Recovery (40% node loss + degradation)",
        n_nodes=120,
        area_size=8000,
        lora_range=2500,
        n_messages=100,
        terrain="urban",
        node_kill_fraction=0.4, # severe partition
        link_degradation=0.4,   # remaining links degraded
        asymmetry=0.2,
        geohash_prefix=4,
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
    net.enable_duty_cycle = getattr(config, 'enable_duty_cycle', False)
    net.enable_collisions = getattr(config, 'enable_collisions', False)

    net.build_topology(
        config.n_nodes, config.area_size, config.lora_range,
        terrain=getattr(config, 'terrain', 'urban'),
        asymmetry=getattr(config, 'asymmetry', 0.0),
        mobile_fraction=getattr(config, 'mobile_fraction', 0.0),
        placement=getattr(config, 'placement', 'random'),
    )
    net.compute_geohash_clusters(config.geohash_prefix)
    net.elect_border_nodes()
    net.run_ogm_round()
    net.compute_routes()
    net.compute_nhs()

    # Apply degradation if configured
    if config.link_degradation > 0:
        net.degrade_links(config.link_degradation)
        net.compute_routes()
        net.compute_nhs()

    # Kill nodes if configured
    if config.node_kill_fraction > 0:
        n_kill = int(len(net.nodes) * config.node_kill_fraction)
        kill_ids = net.rng.sample(list(net.nodes.keys()), n_kill)
        for nid in kill_ids:
            net.kill_node(nid)
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

    Handles mobile node movement every 10 messages, triggering
    link refresh and route recomputation.

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
        node.duty_cycle_blocked = 0
        node.queue.clear()

    # Reset duty cycle and collision trackers
    network.duty_cycle.reset()
    network.collisions.reset()

    # Reset router stats if System5
    if hasattr(router, 'qos_stats'):
        router.qos_stats.clear()
        router.fallback_used = 0
        router.route_switches = 0

    has_mobile = network.mobile_fraction > 0
    mobility_interval = 10  # move nodes every N messages

    for i, (src, dst, priority) in enumerate(messages):
        packet = Packet(src, dst, priority=priority)
        packet.created_at = i
        network.tick = i
        network.sim_time = i * 2.0  # ~2 seconds per message slot

        # Move mobile nodes periodically
        if has_mobile and i > 0 and i % mobility_interval == 0:
            for _ in range(mobility_interval):
                network.move_mobile_nodes(dt=2.0)
            # Recompute routes after movement
            network.compute_routes()
            network.compute_nhs()

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

    # Add duty cycle stats
    if network.enable_duty_cycle:
        result.duty_cycle_violations = network.duty_cycle.violations
        total_blocked = sum(n.duty_cycle_blocked for n in network.nodes.values())
        result.duty_cycle_blocked = total_blocked

    return result


def _run_single_router(args):
    """Worker function for parallel router execution.

    Runs a single router on a scenario. Designed to be called via
    ProcessPoolExecutor — all arguments packed into a single tuple
    for pickling compatibility.

    Args:
        args: (router_key, router_label, router_class_name, config, messages, seed)

    Returns:
        (router_key, router_label, BenchmarkResult)
    """
    router_key, router_label, router_class_name, config, messages, seed = args

    # Reconstruct router class from name (can't pickle classes directly)
    router_classes = {
        "NaiveFloodingRouter": NaiveFloodingRouter,
        "ManagedFloodingRouter": ManagedFloodingRouter,
        "NextHopRouter": NextHopRouter,
        "System5Router": System5Router,
    }
    RouterClass = router_classes[router_class_name]

    net_run = build_network(config, seed=seed)
    router = RouterClass(seed=seed)
    result = run_router(router, net_run, messages)
    return (router_key, router_label, result)


def run_scenario(config, scenario_num, verbose=True, parallel_routers=True):
    """Run a complete scenario with all four routers.

    When parallel_routers=True, each router runs in its own process.

    Args:
        config: ScenarioConfig
        scenario_num: Scenario number for display
        verbose: Whether to print progress
        parallel_routers: Run routers in parallel processes

    Returns:
        Dict with scenario results
    """
    if verbose:
        print(f"\nScenario {scenario_num}: {config.name} "
              f"({config.n_nodes} nodes, {config.area_size/1000:.0f}km)")

    # Build network once to get stats
    if verbose:
        print(f"  Building topology...", end=" ", flush=True)
    net = build_network(config, seed=42)
    net_stats = net.stats_summary()
    if verbose:
        print(f"{net_stats['nodes']} nodes, {net_stats['links']} links")
        print(f"  Clusters: {net_stats['clusters']} | "
              f"Avg routes/dest: {net_stats['avg_routes_per_dest']}")

    # Generate messages (same for all routers)
    messages = generate_messages(net, config.n_messages, seed=42)
    if not messages:
        if verbose:
            print("  WARNING: Not enough alive nodes to generate messages!")
        return None

    # Prepare router jobs
    router_jobs = [
        (key, label, RouterClass.__name__, config, messages, 42)
        for key, label, RouterClass in ROUTER_REGISTRY
    ]

    results_by_router = {}

    if parallel_routers and len(router_jobs) > 1:
        # Run all 4 routers in parallel
        if verbose:
            print(f"  Running 4 routers in parallel...", flush=True)

        with ProcessPoolExecutor(max_workers=min(4, os.cpu_count() or 4)) as executor:
            futures = {
                executor.submit(_run_single_router, job): job[0]
                for job in router_jobs
            }
            for future in as_completed(futures):
                key, label, result = future.result()
                results_by_router[key] = result
                if verbose:
                    print(f"    {label:15s} | Del: {result.delivery_rate:5.1f}% | "
                          f"TX: {result.total_tx:>8} | "
                          f"TX/del: {result.tx_per_delivered:>7.1f} | "
                          f"Hops: {result.avg_hops:.1f}")
    else:
        # Sequential fallback
        for key, label, class_name, cfg, msgs, seed in router_jobs:
            _, _, result = _run_single_router((key, label, class_name, cfg, msgs, seed))
            results_by_router[key] = result
            if verbose:
                print(f"    {label:15s} | Del: {result.delivery_rate:5.1f}% | "
                      f"TX: {result.total_tx:>8} | "
                      f"TX/del: {result.tx_per_delivered:>7.1f} | "
                      f"Hops: {result.avg_hops:.1f}")

    # Comparison
    naive = results_by_router["naive_flooding"]
    managed = results_by_router["managed_flooding"]
    s5 = results_by_router["system5"]

    if verbose and managed.total_tx > 0 and s5.total_tx > 0:
        bw_vs_managed = (1 - s5.total_tx / managed.total_tx) * 100
        bw_vs_naive = (1 - s5.total_tx / naive.total_tx) * 100 if naive.total_tx > 0 else 0
        print(f"  -> System 5 vs Managed Flood: {bw_vs_managed:.1f}% less TX")
        print(f"  -> System 5 vs Naive Flood:   {bw_vs_naive:.1f}% less TX")

    # Compute category
    if config.link_degradation > 0 or config.node_kill_fraction > 0:
        category = "stress"
    elif getattr(config, 'mobile_fraction', 0) > 0:
        category = "mobility"
    elif getattr(config, 'enable_duty_cycle', False):
        category = "duty_cycle"
    elif getattr(config, 'placement', 'random') != 'random':
        category = "topology"
    elif config.n_nodes >= 200:
        category = "dense"
    else:
        category = "scale"

    # Build result dict with all routers
    result_dict = {
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
            "terrain": getattr(config, 'terrain', 'urban'),
            "asymmetry": getattr(config, 'asymmetry', 0.0),
            "mobile_fraction": getattr(config, 'mobile_fraction', 0.0),
            "placement": getattr(config, 'placement', 'random'),
            "enable_duty_cycle": getattr(config, 'enable_duty_cycle', False),
        },
        "network": net_stats,
    }

    for key, label, _ in routers:
        result_dict[key] = results_by_router[key].to_dict()

    # Backward compat aliases
    result_dict["flooding"] = result_dict["managed_flooding"]

    # Comparison vs managed flooding (the real baseline)
    bw_savings_vs_managed = 0.0
    if managed.total_tx > 0:
        bw_savings_vs_managed = round((1 - s5.total_tx / managed.total_tx) * 100, 2)

    result_dict["comparison"] = {
        "bw_savings_pct": bw_savings_vs_managed,
        "bw_savings_vs_naive_pct": round(
            (1 - s5.total_tx / naive.total_tx) * 100, 2
        ) if naive.total_tx > 0 else 0.0,
        "load_reduction_pct": round(
            (1 - s5.max_node_load / managed.max_node_load) * 100, 1
        ) if managed.max_node_load > 0 else 0.0,
        "tx_ratio": round(
            s5.total_tx / managed.total_tx, 4
        ) if managed.total_tx > 0 else 0.0,
    }

    return result_dict


def _run_scenario_worker(args):
    """Worker for parallel scenario execution.

    Args:
        args: (scenario_index, config)

    Returns:
        (scenario_index, result_dict or None)
    """
    scenario_num, config = args
    # Within each worker, run routers sequentially (already in separate process)
    result = run_scenario(config, scenario_num, verbose=False, parallel_routers=False)
    return (scenario_num, result)


def run_all_scenarios(verbose=True, parallel_mode="auto"):
    """Run all benchmark scenarios.

    Parallelization modes:
    - "auto": scenarios in parallel (each runs 4 routers sequentially)
    - "scenarios": parallelize at scenario level
    - "routers": scenarios sequential, routers parallel within each
    - "none": fully sequential

    Args:
        verbose: Whether to print progress
        parallel_mode: Parallelization strategy

    Returns:
        List of scenario result dicts
    """
    n_cpus = os.cpu_count() or 4
    n_scenarios = len(SCENARIOS)

    if verbose:
        print("=" * 60)
        print(f"  MeshRoute Simulator v0.2 — {n_cpus} CPU cores available")
        print("=" * 60)

    if parallel_mode == "auto":
        # Use scenario-level parallelism if we have enough cores
        parallel_mode = "scenarios" if n_cpus >= 4 else "routers"

    if parallel_mode == "scenarios":
        if verbose:
            print(f"  Parallel mode: {n_scenarios} scenarios across "
                  f"{min(n_scenarios, n_cpus)} workers\n")

        results = [None] * n_scenarios
        jobs = [(i + 1, config) for i, config in enumerate(SCENARIOS)]

        with ProcessPoolExecutor(max_workers=min(n_scenarios, n_cpus)) as executor:
            futures = {
                executor.submit(_run_scenario_worker, job): job[0]
                for job in jobs
            }
            completed = 0
            for future in as_completed(futures):
                scenario_num, result = future.result()
                results[scenario_num - 1] = result
                completed += 1
                if verbose and result:
                    s5_tx = result.get("system5", {}).get("total_tx", 0)
                    mg_tx = result.get("managed_flooding", {}).get("total_tx", 1)
                    saving = (1 - s5_tx / mg_tx) * 100 if mg_tx > 0 else 0
                    print(f"  [{completed:2d}/{n_scenarios}] {result['name']:<40s} "
                          f"S5 saves {saving:.1f}% vs Managed")

        results = [r for r in results if r is not None]

    elif parallel_mode == "routers":
        if verbose:
            print(f"  Parallel mode: routers (4 per scenario)\n")
        results = []
        for i, config in enumerate(SCENARIOS, 1):
            result = run_scenario(config, i, verbose=verbose, parallel_routers=True)
            if result:
                results.append(result)

    else:
        # Sequential
        if verbose:
            print(f"  Parallel mode: none (sequential)\n")
        results = []
        for i, config in enumerate(SCENARIOS, 1):
            result = run_scenario(config, i, verbose=verbose, parallel_routers=False)
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
        router_keys = [
            ("naive_flooding", "Naive"),
            ("managed_flooding", "Managed"),
            ("next_hop", "NextHop"),
            ("system5", "Sys5"),
        ]
        for router_key, label in router_keys:
            if router_key not in r:
                continue
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
