"""
SQLite database for MeshRoute simulation results.
Stores all scenario × router × feature combinations persistently.
Supports historical comparisons and feature impact analysis.
"""

import sqlite3
import json
import time
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.db")


def get_db(db_path=None):
    """Get database connection, create tables if needed."""
    conn = sqlite3.connect(db_path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scenarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        n_nodes INTEGER,
        area_size INTEGER,
        lora_range INTEGER,
        n_messages INTEGER,
        terrain TEXT,
        placement TEXT,
        config_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_id INTEGER NOT NULL,
        seed INTEGER NOT NULL,
        features TEXT NOT NULL,
        code_version TEXT,
        timestamp TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (scenario_id) REFERENCES scenarios(id)
    );

    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        router_name TEXT NOT NULL,
        delivery_rate REAL,
        total_tx INTEGER,
        messages_sent INTEGER,
        messages_delivered INTEGER,
        avg_hops REAL,
        max_node_load INTEGER,
        tx_per_delivered REAL,
        fallback_used INTEGER DEFAULT 0,
        route_switches INTEGER DEFAULT 0,
        half_duplex_blocked INTEGER DEFAULT 0,
        qos_json TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id);
    CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs(scenario_id);
    CREATE INDEX IF NOT EXISTS idx_runs_features ON runs(features);
    """)
    conn.commit()


def get_or_create_scenario(conn, config):
    """Find or create a scenario entry. Returns scenario_id."""
    name = config.name
    row = conn.execute(
        "SELECT id FROM scenarios WHERE name = ? AND n_nodes = ?",
        (name, config.n_nodes)
    ).fetchone()
    if row:
        return row["id"]

    config_dict = {
        "n_nodes": config.n_nodes,
        "area_size": config.area_size,
        "lora_range": config.lora_range,
        "n_messages": config.n_messages,
        "terrain": getattr(config, "terrain", "urban"),
        "placement": getattr(config, "placement", "random"),
        "asymmetry": getattr(config, "asymmetry", 0.0),
        "mobile_fraction": getattr(config, "mobile_fraction", 0.0),
        "link_degradation": config.link_degradation,
        "node_kill_fraction": config.node_kill_fraction,
        "geohash_prefix": config.geohash_prefix,
    }
    cur = conn.execute(
        """INSERT INTO scenarios (name, n_nodes, area_size, lora_range, n_messages,
           terrain, placement, config_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, config.n_nodes, config.area_size, config.lora_range,
         config.n_messages, config_dict["terrain"], config_dict["placement"],
         json.dumps(config_dict))
    )
    conn.commit()
    return cur.lastrowid


def store_run(conn, scenario_id, seed, features, code_version="v2.0"):
    """Create a run entry. Returns run_id."""
    cur = conn.execute(
        "INSERT INTO runs (scenario_id, seed, features, code_version) VALUES (?, ?, ?, ?)",
        (scenario_id, seed, features, code_version)
    )
    conn.commit()
    return cur.lastrowid


def store_result(conn, run_id, router_name, bench_result):
    """Store a single router result."""
    qos = {}
    if hasattr(bench_result, "qos_stats") and bench_result.qos_stats:
        qos = {str(k): dict(v) for k, v in bench_result.qos_stats.items()}

    conn.execute(
        """INSERT INTO results (run_id, router_name, delivery_rate, total_tx,
           messages_sent, messages_delivered, avg_hops, max_node_load,
           tx_per_delivered, fallback_used, route_switches, qos_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, router_name, bench_result.delivery_rate, bench_result.total_tx,
         bench_result.messages_sent, bench_result.messages_delivered,
         bench_result.avg_hops, bench_result.max_node_load,
         bench_result.tx_per_delivered,
         getattr(bench_result, "fallback_used", 0),
         getattr(bench_result, "route_switches", 0),
         json.dumps(qos) if qos else None)
    )
    conn.commit()


def run_exists(conn, scenario_id, seed, features):
    """Check if a run already exists."""
    row = conn.execute(
        "SELECT id FROM runs WHERE scenario_id = ? AND seed = ? AND features = ?",
        (scenario_id, seed, features)
    ).fetchone()
    return row["id"] if row else None


def export_json(conn, output_path="results_db_export.json"):
    """Export all results as JSON for the website."""
    rows = conn.execute("""
        SELECT s.name as scenario, s.n_nodes, r.seed, r.features,
               res.router_name, res.delivery_rate, res.total_tx,
               res.avg_hops, res.max_node_load, res.tx_per_delivered,
               res.fallback_used, res.route_switches
        FROM results res
        JOIN runs r ON res.run_id = r.id
        JOIN scenarios s ON r.scenario_id = s.id
        ORDER BY s.name, r.features, res.router_name
    """).fetchall()

    data = [dict(row) for row in rows]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return len(data)


def get_feature_comparison(conn, scenario_name=None):
    """Get delivery rate comparison across feature combinations.
    Returns: {scenario: {features: {router: delivery_rate}}}
    """
    where = ""
    params = []
    if scenario_name:
        where = "WHERE s.name = ?"
        params = [scenario_name]

    rows = conn.execute(f"""
        SELECT s.name, r.features, res.router_name,
               AVG(res.delivery_rate) as avg_delivery,
               AVG(res.total_tx) as avg_tx,
               COUNT(*) as n_seeds
        FROM results res
        JOIN runs r ON res.run_id = r.id
        JOIN scenarios s ON r.scenario_id = s.id
        {where}
        GROUP BY s.name, r.features, res.router_name
        ORDER BY s.name, r.features, res.router_name
    """, params).fetchall()

    result = {}
    for row in rows:
        name = row["name"]
        if name not in result:
            result[name] = {}
        feat = row["features"]
        if feat not in result[name]:
            result[name][feat] = {}
        result[name][feat][row["router_name"]] = {
            "delivery": round(row["avg_delivery"], 1),
            "tx": int(row["avg_tx"]),
            "seeds": row["n_seeds"],
        }
    return result
