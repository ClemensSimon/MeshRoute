"""
Export results database to analysis JSON for the website charts.
Aggregates by scenario × features × router, averaged over seeds.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from results_db import get_db


def export():
    conn = get_db()

    # Aggregated: avg over seeds, grouped by scenario × features × router
    rows = conn.execute("""
        SELECT s.name as scenario, s.n_nodes, r.features,
               res.router_name as router,
               AVG(res.delivery_rate) as delivery,
               AVG(res.total_tx) as tx,
               AVG(res.tx_per_delivered) as tx_per_del,
               AVG(res.avg_hops) as hops,
               AVG(res.max_node_load) as max_load,
               AVG(res.fallback_used) as fallback,
               COUNT(*) as n_seeds
        FROM results res
        JOIN runs r ON res.run_id = r.id
        JOIN scenarios s ON r.scenario_id = s.id
        GROUP BY s.name, r.features, res.router_name
        ORDER BY s.id, r.features, res.router_name
    """).fetchall()

    data = [dict(row) for row in rows]

    # Round for readability
    for d in data:
        d["delivery"] = round(d["delivery"], 1)
        d["tx"] = int(d["tx"])
        d["tx_per_del"] = round(d["tx_per_del"], 1) if d["tx_per_del"] and d["tx_per_del"] != float('inf') else 0
        d["hops"] = round(d["hops"], 1)
        d["max_load"] = int(d["max_load"])
        d["fallback"] = round(d["fallback"], 1)

    # Get scenario order
    scenarios = []
    seen = set()
    for d in data:
        if d["scenario"] not in seen:
            seen.add(d["scenario"])
            scenarios.append({"name": d["scenario"], "n_nodes": d["n_nodes"]})

    output = {
        "scenarios": scenarios,
        "results": data,
        "features": ["baseline", "half_duplex", "hd+silencing"],
        "routers": sorted(set(d["router"] for d in data)),
        "generated_at": __import__("datetime").datetime.now().isoformat(),
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "analysis-data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Exported {len(data)} records ({len(scenarios)} scenarios) to {os.path.abspath(out_path)}")
    conn.close()


if __name__ == "__main__":
    export()
