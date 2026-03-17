"""
MeshRoute Simulator - Entry point.

Usage:
    python run.py                  # run all benchmarks
    python run.py --scenario 1     # run specific scenario (1-5)
    python run.py --visualize      # ASCII visualization of network
    python run.py --scenario 2 --visualize  # specific scenario + visualization
"""

import sys
import os
import argparse
import time

# Ensure the simulator package is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark import SCENARIOS, run_scenario, run_all_scenarios, print_summary_table, save_results
from meshsim import MeshNetwork


def visualize_scenario(scenario_num):
    """Build and visualize a scenario's network topology."""
    if scenario_num < 1 or scenario_num > len(SCENARIOS):
        print(f"Error: scenario must be 1-{len(SCENARIOS)}")
        return

    config = SCENARIOS[scenario_num - 1]
    print(f"\nVisualizing: Scenario {scenario_num} - {config.name}")
    print(f"  {config.n_nodes} nodes, {config.area_size/1000:.0f}km area, "
          f"{config.lora_range}m LoRa range\n")

    from benchmark import build_network
    net = build_network(config, seed=42)
    print(net.ascii_visualization())

    stats = net.stats_summary()
    print(f"\n  Nodes: {stats['nodes']} | Links: {stats['links']} | "
          f"Clusters: {stats['clusters']}")
    print(f"  Avg neighbors: {stats['avg_neighbors']} | "
          f"Avg routes/dest: {stats['avg_routes_per_dest']}")

    # Show cluster details
    for cid, cluster in sorted(net.clusters.items()):
        print(f"  Cluster {cid} ('{cluster.geohash_prefix}'): "
              f"{len(cluster.members)} nodes, "
              f"{len(cluster.border_nodes)} border")


def main():
    parser = argparse.ArgumentParser(
        description="MeshRoute System 5 Routing Simulator"
    )
    parser.add_argument(
        "--scenario", "-s",
        type=int,
        default=0,
        help=f"Run specific scenario (1-{len(SCENARIOS)}). 0 = all."
    )
    parser.add_argument(
        "--visualize", "-v",
        action="store_true",
        help="Show ASCII visualization of the network topology"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="results.json",
        help="Output JSON file for results (default: results.json)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output"
    )

    args = parser.parse_args()
    verbose = not args.quiet

    start_time = time.time()

    # Visualization mode
    if args.visualize:
        scenario_num = args.scenario if args.scenario > 0 else 1
        visualize_scenario(scenario_num)
        if args.scenario == 0:
            # Also visualize all if no specific scenario
            for i in range(2, len(SCENARIOS) + 1):
                visualize_scenario(i)
        print()

    # Benchmark mode
    if args.scenario > 0:
        # Single scenario
        if args.scenario < 1 or args.scenario > len(SCENARIOS):
            print(f"Error: scenario must be 1-{len(SCENARIOS)}")
            sys.exit(1)

        if verbose:
            print("=" * 50)
            print("  MeshRoute Simulator v0.1")
            print("=" * 50)

        config = SCENARIOS[args.scenario - 1]
        result = run_scenario(config, args.scenario, verbose=verbose)
        results = [result] if result else []
    elif not args.visualize:
        # All scenarios (default when no --visualize)
        results = run_all_scenarios(verbose=verbose)
    elif args.visualize and args.scenario == 0:
        # --visualize without --scenario: show visuals + run all
        results = run_all_scenarios(verbose=verbose)
    else:
        results = []

    # Print summary and save
    if results:
        print_summary_table(results)

        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), args.output
        )
        save_results(results, output_path)

    elapsed = time.time() - start_time
    if verbose:
        print(f"\nTotal time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
