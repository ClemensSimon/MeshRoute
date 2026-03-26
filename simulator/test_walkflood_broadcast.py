"""
Test WalkFloodBroadcast vs ManagedFloodBroadcast on Bay Area scenario.

Runs with half-duplex disabled to isolate broadcast algorithm differences,
then with half-duplex enabled to show real-world Bay Area behavior.
"""

import sys
import os
import time
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meshsim import MeshNetwork
from benchmark import SCENARIOS
from routing import ManagedFloodBroadcast, WalkFloodBroadcast, WalkFloodRouter


def build_network(config, seed=42, half_duplex=None):
    """Build a fresh network."""
    net = MeshNetwork(seed=seed)
    net.enable_duty_cycle = False
    net.enable_collisions = getattr(config, 'enable_collisions', False)
    if half_duplex is not None:
        net.enable_half_duplex = half_duplex
    else:
        net.enable_half_duplex = getattr(config, 'enable_half_duplex', False)
    net.build_topology(
        config.n_nodes, config.area_size, config.lora_range,
        terrain=getattr(config, 'terrain', 'urban'),
        asymmetry=getattr(config, 'asymmetry', 0.0),
        placement=getattr(config, 'placement', 'random'),
    )
    net.compute_geohash_clusters(config.geohash_prefix)
    net.elect_border_nodes()
    net.run_ogm_round()
    net.compute_routes()
    net.compute_nhs()
    return net


def run_test(config, half_duplex, src_nodes, alive_nodes, label):
    """Run all broadcast methods and return results."""
    rng = random.Random(99)
    results = {}

    for method_key, method_label, run_fn in [
        ('mf', 'Managed Flood', lambda net, src, i: ManagedFloodBroadcast(seed=42+i).broadcast(net, src)),
        ('mpr', 'WalkFlood MPR', lambda net, src, i: WalkFloodBroadcast(seed=42+i).broadcast_mpr(net, src)),
        ('sc3', 'Scoped (3-hop)', lambda net, src, i: WalkFloodBroadcast(seed=42+i).broadcast_scoped(net, src, max_hops=3)),
        ('sc5', 'Scoped (5-hop)', lambda net, src, i: WalkFloodBroadcast(seed=42+i).broadcast_scoped(net, src, max_hops=5)),
    ]:
        stats_list = []
        for i, src_id in enumerate(src_nodes):
            net = build_network(config, seed=42, half_duplex=half_duplex)
            s = run_fn(net, src_id, i)
            stats_list.append(s)
        results[method_key] = (method_label, stats_list)

    # Pull-based
    pull_stats = []
    for i, src_id in enumerate(src_nodes):
        net = build_network(config, seed=42, half_duplex=half_duplex)
        targets = rng.sample([n for n in alive_nodes if n != src_id], min(10, len(alive_nodes)-1))
        wf = WalkFloodBroadcast(seed=42+i)
        s = wf.pull_telemetry(net, src_id, targets)
        pull_stats.append(s)
    results['pull'] = ('Pull (10 targets)', pull_stats)

    return results


def print_results(results, label):
    print(f"\n{'='*75}")
    print(f"  {label}")
    print(f"{'='*75}")

    mf_tx = sum(s.total_tx for s in results['mf'][1]) / len(results['mf'][1])

    print(f"{'Method':<35s} {'Reach%':>8s} {'Avg TX':>10s} {'TX vs MF':>12s}")
    print("-" * 68)

    for key in ['mf', 'mpr', 'sc3', 'sc5']:
        label_str, stats_list = results[key]
        reach = sum(s.reach_pct for s in stats_list) / len(stats_list)
        tx = sum(s.total_tx for s in stats_list) / len(stats_list)
        savings = 100 * (1 - tx / mf_tx) if mf_tx > 0 else 0
        sav_str = f"{savings:+.1f}%" if key != 'mf' else '---'
        print(f"{label_str:<35s} {reach:>7.1f}% {tx:>10,.0f} {sav_str:>12s}")

    # Pull
    _, pull_stats = results['pull']
    pull_tx = sum(s.total_tx for s in pull_stats) / len(pull_stats)
    pull_del = sum(len(s.nodes_reached)-1 for s in pull_stats) / len(pull_stats)
    pull_sav = 100 * (1 - pull_tx / mf_tx) if mf_tx > 0 else 0
    print(f"{'Pull-based (10 unicasts)':<35s} {pull_del:>5.1f}/10 {pull_tx:>10,.0f} {pull_sav:>+11.1f}%")


def main():
    config = SCENARIOS[22]  # Bay Area Mesh (3-tier, 235 nodes)
    print(f"=== WalkFlood Broadcast Benchmark ===")
    print(f"Scenario: {config.name}")
    print(f"Nodes: {config.n_nodes}, Area: {config.area_size}m, Range: {config.lora_range}m")

    # Build reference network to pick test nodes
    net_ref = build_network(config, seed=42, half_duplex=False)
    alive = [nid for nid, n in net_ref.nodes.items() if n.battery > 0 and n.neighbors]

    # Stratified sample across tiers
    rng = random.Random(42)
    by_tier = {'mountain': [], 'hill': [], 'valley': []}
    for nid in alive:
        tier = getattr(net_ref.nodes[nid], 'node_tier', 'valley')
        by_tier[tier].append(nid)

    src_nodes = []
    for tier_name, count in [('mountain', 2), ('hill', 3), ('valley', 5)]:
        nodes = by_tier[tier_name]
        if nodes:
            src_nodes.extend(rng.sample(nodes, min(count, len(nodes))))

    print(f"\nSource nodes ({len(src_nodes)}):")
    for nid in src_nodes:
        n = net_ref.nodes[nid]
        tier = getattr(n, 'node_tier', '?')
        avg_q = sum(n.neighbors.values()) / len(n.neighbors) if n.neighbors else 0
        print(f"  Node {nid:>3d}: {tier:<8s} {len(n.neighbors):>3d} neighbors, avg_q={avg_q:.3f}")

    # ---------------------------------------------------------------
    # Test 1: Without half-duplex (shows pure algorithm differences)
    # ---------------------------------------------------------------
    t0 = time.time()
    results_no_hd = run_test(config, half_duplex=False, src_nodes=src_nodes,
                              alive_nodes=alive, label="No Half-Duplex")
    print_results(results_no_hd, "WITHOUT Half-Duplex (pure algorithm comparison)")

    # Per-node detail for MPR
    print("\n  Per-node MPR detail (no half-duplex):")
    mf_list = results_no_hd['mf'][1]
    mpr_list = results_no_hd['mpr'][1]
    for i, src_id in enumerate(src_nodes):
        tier = getattr(net_ref.nodes[src_id], 'node_tier', '?')
        mf_s = mf_list[i]
        mpr_s = mpr_list[i]
        sav = 100*(1-mpr_s.total_tx/mf_s.total_tx) if mf_s.total_tx > 0 else 0
        print(f"    [{tier:>8s}] Node {src_id:>3d}: "
              f"MF={mf_s.reach_pct:5.1f}%/{mf_s.total_tx:>5d}tx  "
              f"MPR={mpr_s.reach_pct:5.1f}%/{mpr_s.total_tx:>5d}tx ({sav:+.0f}%)")

    # ---------------------------------------------------------------
    # Test 2: With half-duplex (real-world Bay Area)
    # ---------------------------------------------------------------
    results_hd = run_test(config, half_duplex=True, src_nodes=src_nodes,
                           alive_nodes=alive, label="Half-Duplex")
    print_results(results_hd, "WITH Half-Duplex (real-world Bay Area)")

    elapsed = time.time() - t0
    print(f"\nTotal test time: {elapsed:.1f}s")

    # ---------------------------------------------------------------
    # MPR Set Analysis
    # ---------------------------------------------------------------
    print(f"\n{'='*75}")
    print("  MPR SET ANALYSIS")
    print(f"{'='*75}")
    wf = WalkFloodBroadcast(seed=42)
    wf._compute_mpr_sets(net_ref)

    for tier_name in ['mountain', 'hill', 'valley']:
        tier_nodes = by_tier[tier_name]
        if not tier_nodes:
            continue
        mpr_sizes = [len(wf._mpr_sets.get(nid, set())) for nid in tier_nodes]
        nb_sizes = [len(net_ref.nodes[nid].neighbors) for nid in tier_nodes]
        avg_mpr = sum(mpr_sizes) / len(mpr_sizes)
        avg_nb = sum(nb_sizes) / len(nb_sizes)
        reduction = 100 * (1 - avg_mpr / avg_nb) if avg_nb > 0 else 0
        print(f"  {tier_name:<10s}: {len(tier_nodes):>3d} nodes, "
              f"avg neighbors={avg_nb:>5.1f}, avg MPR={avg_mpr:>4.1f} "
              f"({reduction:.0f}% reduction)")


if __name__ == "__main__":
    main()
