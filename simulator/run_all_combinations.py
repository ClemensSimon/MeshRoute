"""
Run ALL scenario × router × feature combinations and store in SQLite DB.

Feature combinations per scenario:
1. baseline        — no half-duplex, no silencing
2. half_duplex     — half-duplex + collisions (realistic radio)
3. hd+silencing    — half-duplex + collisions + node silencing

Each combination runs with multiple seeds for statistical reliability.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark import (
    SCENARIOS, ScenarioConfig, build_network, generate_messages,
    run_router, BenchmarkResult, ROUTER_REGISTRY
)
from meshsim import MeshNetwork, Packet
from results_db import get_db, get_or_create_scenario, store_run, store_result, run_exists


# Feature configurations to test
FEATURE_CONFIGS = [
    {
        "name": "baseline",
        "half_duplex": False,
        "collisions": False,
        "silencing": False,
    },
    {
        "name": "half_duplex",
        "half_duplex": True,
        "collisions": True,
        "silencing": False,
    },
    {
        "name": "hd+silencing",
        "half_duplex": True,
        "collisions": True,
        "silencing": True,
    },
]

SEEDS = [42, 123, 256, 789, 1337]

# Skip slow scenarios
SKIP_SCENARIOS = {"Building Emergency (high density, high load)"}


def make_config_variant(base_config, features):
    """Create a scenario config with specific feature flags."""
    return ScenarioConfig(
        name=base_config.name,
        n_nodes=base_config.n_nodes,
        area_size=base_config.area_size,
        lora_range=base_config.lora_range,
        n_messages=base_config.n_messages,
        link_degradation=base_config.link_degradation,
        node_kill_fraction=base_config.node_kill_fraction,
        geohash_prefix=base_config.geohash_prefix,
        terrain=getattr(base_config, "terrain", "urban"),
        asymmetry=getattr(base_config, "asymmetry", 0.0),
        mobile_fraction=getattr(base_config, "mobile_fraction", 0.0),
        placement=getattr(base_config, "placement", "random"),
        enable_duty_cycle=getattr(base_config, "enable_duty_cycle", False),
        enable_half_duplex=features["half_duplex"],
        enable_collisions=features["collisions"],
        enable_silencing=features["silencing"],
        silence_fraction=getattr(base_config, "silence_fraction", 0.6),
    )


def get_base_scenarios():
    """Get unique base scenarios (deduplicate Bay Area variants)."""
    seen = set()
    bases = []
    for config in SCENARIOS:
        # Use base name without silencing/stress variants
        base_name = config.name
        if "Silencing" in base_name:
            continue  # skip, we handle silencing as a feature flag
        if base_name in seen or base_name in SKIP_SCENARIOS:
            continue
        seen.add(base_name)
        bases.append(config)
    return bases


def run_combination(conn, config, features, seed):
    """Run one scenario × features × seed combination. Store results."""
    scenario_id = get_or_create_scenario(conn, config)
    feat_name = features["name"]

    # Check if already computed
    existing = run_exists(conn, scenario_id, seed, feat_name)
    if existing:
        return None  # skip

    # Build network with feature flags
    variant = make_config_variant(config, features)
    net = build_network(variant, seed=seed)
    msgs = generate_messages(net, config.n_messages, seed=seed)

    if not msgs:
        return None

    # Create run entry
    run_id = store_run(conn, scenario_id, seed, feat_name)

    # Run each router
    for key, label, RouterClass, kwargs in ROUTER_REGISTRY:
        # Reset network state
        Packet._next_id = 0
        for node in net.nodes.values():
            node.packets_sent = 0
            node.packets_forwarded = 0
            node.packets_received = 0
            node.queue.clear()
        net.duty_cycle.reset()
        net.collisions.reset()
        net.half_duplex.reset()

        router = RouterClass(seed=seed, **kwargs)
        result = run_router(router, net, msgs)

        # Copy extended stats
        if hasattr(router, "qos_stats"):
            result.qos_stats = {k: dict(v) for k, v in router.qos_stats.items()}
            result.fallback_used = router.fallback_used
            result.route_switches = router.route_switches

        store_result(conn, run_id, label, result)

    return run_id


def main():
    conn = get_db()
    base_scenarios = get_base_scenarios()

    total = len(base_scenarios) * len(FEATURE_CONFIGS) * len(SEEDS)
    print(f"=== MeshRoute Full Combination Run ===")
    print(f"Scenarios: {len(base_scenarios)}")
    print(f"Feature configs: {len(FEATURE_CONFIGS)} ({', '.join(f['name'] for f in FEATURE_CONFIGS)})")
    print(f"Seeds: {len(SEEDS)}")
    print(f"Routers per run: {len(ROUTER_REGISTRY)}")
    print(f"Total combinations: {total}")
    print(f"Total router runs: {total * len(ROUTER_REGISTRY)}")
    print()

    done = 0
    skipped = 0
    start = time.time()

    for si, config in enumerate(base_scenarios, 1):
        for fi, features in enumerate(FEATURE_CONFIGS):
            for seed in SEEDS:
                done += 1
                result = run_combination(conn, config, features, seed)
                if result is None:
                    skipped += 1
                    continue
                elapsed = time.time() - start
                rate = (done - skipped) / max(elapsed, 1)
                remaining = (total - done) / max(rate, 0.01)
                print(
                    f"  [{done:3d}/{total}] {config.name[:35]:<35s} "
                    f"{features['name']:<14s} seed={seed:4d}  "
                    f"({elapsed:.0f}s, ~{remaining:.0f}s left)"
                )

    conn.close()

    elapsed = time.time() - start
    print(f"\nDone: {done - skipped} computed, {skipped} skipped (already in DB)")
    print(f"Total time: {elapsed:.0f}s")
    print(f"Database: {os.path.abspath(DB_PATH)}")


if __name__ == "__main__":
    from results_db import DB_PATH
    main()
