"""
Routing algorithms for MeshRoute simulator.

Implements four routing strategies:
1. NaiveFloodingRouter  — Pure flood baseline (every node rebroadcasts)
2. ManagedFloodingRouter — Meshtastic's actual approach (SNR-based suppression)
3. NextHopRouter        — Meshtastic v2.6 directed messaging (learn + cache relay)
4. System5Router        — Geo-clustered multi-path load-balanced routing
"""

import random
import math
from collections import defaultdict

from lora_model import packet_success_rate, time_on_air
from meshsim import Packet


# Default time-on-air for a typical 50-byte LoRa packet at SF7
DEFAULT_TOA = time_on_air(50, sf=7)


class RoutingStats:
    """Statistics from routing a single packet."""

    def __init__(self):
        self.delivered = False
        self.total_tx = 0  # total transmissions across all nodes
        self.hops = 0  # hops to destination (0 if not delivered)
        self.path = []  # actual path taken
        self.energy = 0.0  # energy consumed (proportional to tx count)
        self.node_tx_counts = defaultdict(int)  # per-node transmission count
        self.half_duplex_blocked = 0  # times TX was blocked by half-duplex

    def __repr__(self):
        status = "OK" if self.delivered else "FAIL"
        return f"Stats({status}, tx={self.total_tx}, hops={self.hops})"


# ============================================================================
# 1. NAIVE FLOODING (reference baseline)
# ============================================================================

class NaiveFloodingRouter:
    """Pure naive flooding: every node rebroadcasts every packet once.

    No intelligence. Used only as a theoretical worst-case reference.
    Uses Meshtastic's default hop limit of 7.
    """

    MESHTASTIC_HOP_LIMIT = 7

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self.hop_limit = hop_limit or self.MESHTASTIC_HOP_LIMIT

    def route(self, network, packet):
        stats = RoutingStats()

        if packet.src not in network.nodes or packet.dst not in network.nodes:
            return stats
        if network.nodes[packet.src].battery <= 0:
            return stats
        if not network.nodes[packet.src].neighbors:
            return stats

        seen = {packet.src}
        broadcast_queue = [(packet.src, 0, [packet.src])]
        delivery_path = None
        hop_limit = self.hop_limit
        sim_time = network.sim_time

        while broadcast_queue:
            current_id, hop_count, path = broadcast_queue.pop(0)
            if hop_count >= hop_limit:
                continue

            current_node = network.nodes[current_id]

            # Silenced nodes do NOT rebroadcast (but can still receive)
            if current_node.silent and current_id != packet.src and current_id != packet.dst:
                continue

            # Half-duplex: skip this rebroadcast if node is busy receiving
            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    continue  # node blocked, can't rebroadcast

            for neighbor_id, quality in current_node.neighbors.items():
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                current_node.packets_forwarded += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue

                seen.add(neighbor_id)
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    continue

                neighbor_node = network.nodes[neighbor_id]
                if neighbor_node.battery <= 0 or not neighbor_node.neighbors:
                    continue

                broadcast_queue.append((neighbor_id, hop_count + 1, new_path))

            # Half-duplex: mark TX and neighbors as RX
            if network.enable_half_duplex:
                toa = time_on_air(packet.payload_size, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, sim_time, toa)
                sim_time += toa * 0.3  # partial overlap (managed flooding staggers)

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1

        stats.energy = stats.total_tx
        return stats


# ============================================================================
# 2. MANAGED FLOODING (Meshtastic's actual current approach)
# ============================================================================

class ManagedFloodingRouter:
    """Meshtastic-style managed flooding with SNR-based suppression.

    Key mechanisms:
    - Before rebroadcasting, nodes listen for other rebroadcasts
    - SNR-based priority: distant nodes (low SNR) rebroadcast first
    - Closer nodes suppress if they hear a rebroadcast
    - ROUTER-role nodes always rebroadcast regardless
    - Duplicate detection via packet ID tracking
    - Dynamic broadcast interval scaling for large networks (40+ nodes)
    - Uses Meshtastic's default hop limit of 7
    """

    # Fraction of nodes assigned ROUTER role (always rebroadcast)
    ROUTER_FRACTION = 0.05  # ~5% of nodes are routers

    # Probability that a non-router node suppresses after hearing a rebroadcast
    # Depends on SNR: high SNR (close) = high suppression, low SNR (far) = low
    SUPPRESSION_BASE = 0.6

    MESHTASTIC_HOP_LIMIT = 7

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self.hop_limit = hop_limit or self.MESHTASTIC_HOP_LIMIT
        self._router_nodes = set()

    def _assign_router_roles(self, network):
        """Assign ROUTER role to a fraction of nodes (those with most neighbors)."""
        if self._router_nodes:
            return
        nodes_by_neighbors = sorted(
            network.nodes.values(),
            key=lambda n: len(n.neighbors),
            reverse=True,
        )
        n_routers = max(1, int(len(nodes_by_neighbors) * self.ROUTER_FRACTION))
        self._router_nodes = {n.id for n in nodes_by_neighbors[:n_routers]}

    def _suppression_probability(self, link_quality):
        """Higher link quality (closer node) = higher probability of suppression.

        Distant nodes (low quality/SNR) have low suppression = they rebroadcast first.
        Close nodes (high quality/SNR) have high suppression = they wait and suppress.
        """
        # link_quality is 0-1 where higher = closer/better signal
        return self.SUPPRESSION_BASE * link_quality

    def route(self, network, packet):
        stats = RoutingStats()

        if packet.src not in network.nodes or packet.dst not in network.nodes:
            return stats
        src_node = network.nodes[packet.src]
        if src_node.battery <= 0 or not src_node.neighbors:
            return stats

        self._assign_router_roles(network)

        # Track which nodes have seen the packet and which have rebroadcast it
        seen = {packet.src}
        rebroadcasted = {packet.src}  # nodes that actually transmitted

        # BFS queue: (node_id, hop_count, path, receiving_link_quality)
        broadcast_queue = [(packet.src, 0, [packet.src], 1.0)]
        delivery_path = None
        hop_limit = self.hop_limit
        sim_time = network.sim_time

        while broadcast_queue:
            current_id, hop_count, path, recv_quality = broadcast_queue.pop(0)
            if hop_count >= hop_limit:
                continue

            current_node = network.nodes[current_id]

            # Silenced nodes do NOT rebroadcast (but can still receive)
            if current_node.silent and current_id != packet.src and current_id != packet.dst:
                continue

            # Half-duplex: skip rebroadcast if node is busy receiving
            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    continue

            # Decision: should this node rebroadcast?
            is_router = current_id in self._router_nodes
            should_rebroadcast = True

            if not is_router and current_id != packet.src:
                # Check if any neighbor already rebroadcasted (suppression)
                neighbor_rebroadcasted = any(
                    nid in rebroadcasted for nid in current_node.neighbors
                    if nid != packet.src and nid in seen
                )
                if neighbor_rebroadcasted:
                    # SNR-based suppression: closer nodes more likely to suppress
                    suppress_prob = self._suppression_probability(recv_quality)
                    if self.rng.random() < suppress_prob:
                        should_rebroadcast = False

            if not should_rebroadcast:
                continue

            rebroadcasted.add(current_id)

            # Broadcast to all neighbors
            for neighbor_id, quality in current_node.neighbors.items():
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                current_node.packets_forwarded += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue

                seen.add(neighbor_id)
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    continue

                neighbor_node = network.nodes[neighbor_id]
                if neighbor_node.battery <= 0 or not neighbor_node.neighbors:
                    continue

                broadcast_queue.append((neighbor_id, hop_count + 1, new_path, quality))

            # Half-duplex: mark TX and neighbors as RX
            if network.enable_half_duplex:
                toa = time_on_air(packet.payload_size, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, sim_time, toa)
                sim_time += toa * 0.3  # staggered managed flooding

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1

        stats.energy = stats.total_tx
        return stats


# Keep backward compatibility alias
FloodingRouter = ManagedFloodingRouter


# ============================================================================
# 3. NEXT-HOP ROUTING (Meshtastic v2.6 for direct messages)
# ============================================================================

class NextHopRouter:
    """Meshtastic v2.6 next-hop routing for direct messages.

    Mechanism:
    1. First message to a destination uses managed flooding
    2. System tracks which relay node successfully delivered
    3. Subsequent messages use only that one relay node (next-hop)
    4. Falls back to managed flooding if next-hop fails

    This is only for unicast/direct messages, not broadcasts.
    """

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self._managed = ManagedFloodingRouter(seed=seed, hop_limit=hop_limit)
        # Cache: (src, dst) -> next_hop_node_id
        self._next_hop_cache = {}

    def route(self, network, packet):
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0 or not src_node.neighbors:
            return stats
        if packet.dst not in network.nodes:
            return stats

        cache_key = (packet.src, packet.dst)

        # Try cached next-hop first
        if cache_key in self._next_hop_cache:
            next_hop_id = self._next_hop_cache[cache_key]
            next_hop_node = network.nodes.get(next_hop_id)

            if (next_hop_node and next_hop_node.battery > 0
                    and next_hop_id in src_node.neighbors):
                # Try forwarding via next-hop
                quality = src_node.neighbors[next_hop_id]
                stats.total_tx += 1
                stats.node_tx_counts[packet.src] += 1

                if self.rng.random() <= quality:
                    # Next-hop received it — now it does managed flood from there
                    relay_packet = type(packet)(next_hop_id, packet.dst,
                                                packet.priority, packet.payload_size)
                    relay_packet.ttl = packet.ttl - 1
                    relay_packet.created_at = packet.created_at

                    relay_stats = self._managed.route(network, relay_packet)

                    stats.total_tx += relay_stats.total_tx
                    for nid, count in relay_stats.node_tx_counts.items():
                        stats.node_tx_counts[nid] += count

                    if relay_stats.delivered:
                        stats.delivered = True
                        stats.hops = relay_stats.hops + 1
                        stats.path = [packet.src] + relay_stats.path
                        packet.hops = stats.path
                        packet.delivered_at = network.tick
                        stats.energy = stats.total_tx
                        return stats

                # Next-hop failed — invalidate cache, fall through to flooding
                del self._next_hop_cache[cache_key]

        # Fall back to managed flooding
        flood_stats = self._managed.route(network, packet)

        stats.total_tx += flood_stats.total_tx
        stats.delivered = flood_stats.delivered
        stats.hops = flood_stats.hops
        stats.path = flood_stats.path
        stats.energy = flood_stats.total_tx
        for nid, count in flood_stats.node_tx_counts.items():
            stats.node_tx_counts[nid] += count

        # Learn next-hop from successful delivery path
        if flood_stats.delivered and len(flood_stats.path) >= 3:
            # The first relay in the path becomes our next-hop
            self._next_hop_cache[cache_key] = flood_stats.path[1]

        return stats


# ============================================================================
# 4. SYSTEM 5 — GEO-CLUSTERED MULTI-PATH LOAD-BALANCED ROUTING
# ============================================================================

class System5Router:
    """System 5: Geo-clustered multi-path load-balanced routing.

    Features:
    - Uses pre-computed multi-path routes
    - Weighted route selection: W(r) = alpha*Q + beta*(1-Load) + gamma*Batt
    - Proportional load distribution across routes
    - QoS gate based on local Network Health Score
    - Back-pressure: avoids overloaded nodes
    - Fallback: scoped cluster flooding when all routes fail
    """

    ALPHA = 0.4
    BETA = 0.35
    GAMMA = 0.25

    NHS_THRESHOLDS = {
        0.8: 7,
        0.6: 5,
        0.4: 3,
        0.2: 1,
        0.0: 0,
    }

    BACKPRESSURE_THRESHOLD = 0.8
    MAX_RETRIES = 3  # 3 attempts per hop (was 2)

    PROBE_STALE_TICKS = 3      # probe routes not used in 3+ ticks
    PROBE_TIMEOUT_TICKS = 2    # probe considered failed after 2 ticks

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.qos_stats = defaultdict(lambda: {"sent": 0, "delivered": 0})
        self.fallback_used = 0
        self.route_switches = 0
        self.probes_sent = 0
        self.probes_succeeded = 0
        self.probes_failed = 0
        self.routes_killed_by_probe = 0

    def _qos_gate(self, node, packet):
        nhs = node.nhs
        max_priority = 0
        for threshold, priority in sorted(self.NHS_THRESHOLDS.items(), reverse=True):
            if nhs >= threshold:
                max_priority = priority
                break
        return packet.priority <= max_priority

    def _select_route(self, routes, network):
        valid_routes = []
        for route in routes:
            alive = True
            for nid in route.path:
                if network.nodes[nid].battery <= 0:
                    alive = False
                    break
            if not alive:
                continue

            for i in range(len(route.path) - 1):
                a, b = route.path[i], route.path[i + 1]
                link = network.get_link(a, b)
                if not link or not link.alive:
                    alive = False
                    break
            if not alive:
                continue

            quality = 1.0
            for i in range(len(route.path) - 1):
                link = network.get_link(route.path[i], route.path[i + 1])
                if link:
                    quality *= link.quality

            intermediates = route.path[1:-1]
            if intermediates:
                loads = [network.nodes[nid].load() for nid in intermediates]
                avg_load = sum(loads) / len(loads)
                # Gradual backpressure: penalize weight instead of hard cutoff
                # Only fully block if ALL intermediates are saturated (>0.95)
                if min(loads) > 0.95:
                    continue
            else:
                avg_load = 0.0

            batteries = [network.nodes[nid].battery_score() for nid in route.path[1:]]
            min_batt = min(batteries) if batteries else 1.0

            route.quality = quality
            route.load = avg_load
            route.battery = min_batt
            route.compute_weight(self.ALPHA, self.BETA, self.GAMMA)

            if route.weight > 0:
                valid_routes.append(route)

        if not valid_routes:
            return None

        total_weight = sum(r.weight for r in valid_routes)
        if total_weight <= 0:
            return self.rng.choice(valid_routes)

        r = self.rng.uniform(0, total_weight)
        cumulative = 0
        for route in valid_routes:
            cumulative += route.weight
            if r <= cumulative:
                return route

        return valid_routes[-1]

    def _try_route(self, network, packet, path, stats):
        current_path = [path[0]]
        sim_time = network.sim_time

        for i in range(len(path) - 1):
            current_id = path[i]
            next_id = path[i + 1]

            current_node = network.nodes[current_id]
            link = network.get_link(current_id, next_id)

            # Half-duplex check: can this node transmit right now?
            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    # Wait briefly and retry once (node finishes RX)
                    sim_time += DEFAULT_TOA * 2
                    if not network.half_duplex.can_transmit(current_id, sim_time):
                        return False  # still blocked, route fails

            stats.total_tx += 1
            stats.node_tx_counts[current_id] += 1
            current_node.packets_forwarded += 1
            current_node.battery = max(0, current_node.battery - 0.01)

            # Mark TX and all neighbors as RX (half-duplex: they hear this TX)
            toa = time_on_air(packet.payload_size, sf=current_node.sf)
            if network.enable_half_duplex:
                network.half_duplex.start_tx(current_id, sim_time, toa)
                # All neighbors within range hear this TX and are blocked from TX
                for neighbor_id in current_node.neighbors:
                    network.half_duplex.start_rx(neighbor_id, sim_time, toa)
                sim_time += toa  # advance time

            quality = link.quality if link else 0.1
            delivered_hop = False
            # Adaptive retries: more attempts for poor links
            max_retries = self.MAX_RETRIES if quality > 0.5 else self.MAX_RETRIES + 2
            for retry in range(max_retries):
                if self.rng.random() <= quality:
                    delivered_hop = True
                    break
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                if network.enable_half_duplex:
                    sim_time += toa  # each retry takes time

            if not delivered_hop:
                return False

            next_node = network.nodes[next_id]
            if next_node.battery <= 0:
                return False

            current_path.append(next_id)

            next_node.queue.append(packet.id)
            if len(next_node.queue) > 50:
                next_node.queue.pop(0)

            if next_id == packet.dst:
                stats.delivered = True
                stats.hops = len(current_path) - 1
                stats.path = current_path
                packet.hops = current_path
                packet.delivered_at = network.tick
                next_node.packets_received += 1
                return True

            if not self._qos_gate(next_node, packet):
                return False

        return False

    def _fallback_cluster_flood(self, network, packet, stats):
        """Scoped flooding along cluster corridor from src to dst.
        Finds shortest cluster-level path, then floods border nodes
        and their neighborhoods along that corridor."""
        src_node = network.nodes[packet.src]
        dst_node = network.nodes[packet.dst]
        src_cid = src_node.cluster_id
        dst_cid = dst_node.cluster_id

        # 1. Find cluster-level adjacency and shortest cluster path
        cluster_adj = defaultdict(set)
        for cluster in network.clusters.values():
            for nid in cluster.border_nodes:
                for neighbor_id in network.nodes[nid].neighbors:
                    ncid = network.nodes[neighbor_id].cluster_id
                    if ncid != cluster.id:
                        cluster_adj[cluster.id].add(ncid)

        # BFS on cluster graph to find corridor
        corridor_cids = set()
        c_visited = {src_cid}
        c_queue = [(src_cid, [src_cid])]
        c_path = None
        while c_queue:
            ccur, cpath = c_queue.pop(0)
            if ccur == dst_cid:
                c_path = cpath
                break
            for cnext in cluster_adj.get(ccur, []):
                if cnext not in c_visited:
                    c_visited.add(cnext)
                    c_queue.append((cnext, cpath + [cnext]))

        if c_path:
            corridor_cids = set(c_path)
        else:
            corridor_cids = {src_cid, dst_cid}

        # 2. Build flood scope: all nodes in src/dst clusters + border nodes of corridor
        flood_nodes = set()

        # Source and destination cluster members (small clusters now, ~50 max)
        for cid in [src_cid, dst_cid]:
            if cid is not None and cid in network.clusters:
                for nid in network.clusters[cid].members:
                    if network.nodes[nid].battery > 0:
                        flood_nodes.add(nid)

        # Border nodes + neighbors along the corridor
        for cid in corridor_cids:
            if cid is not None and cid in network.clusters:
                for nid in network.clusters[cid].border_nodes:
                    if network.nodes[nid].battery > 0:
                        flood_nodes.add(nid)
                        for neighbor_id in network.nodes[nid].neighbors:
                            if network.nodes[neighbor_id].battery > 0:
                                flood_nodes.add(neighbor_id)

        seen = {packet.src}
        queue = [(packet.src, 0, [packet.src])]
        delivery_path = None

        while queue:
            current_id, hop_count, path = queue.pop(0)
            if hop_count >= min(packet.ttl, 20):  # higher hop limit for fallback
                continue

            current_node = network.nodes[current_id]

            # Silenced nodes skip rebroadcast even in fallback flood
            if current_node.silent and current_id != packet.src and current_id != packet.dst:
                continue

            for neighbor_id, quality in current_node.neighbors.items():
                if neighbor_id not in flood_nodes:
                    continue

                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue
                seen.add(neighbor_id)
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    continue

                queue.append((neighbor_id, hop_count + 1, new_path))

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1
            return True
        return False

    def probe_secondary_routes(self, network, src_node, stats):
        """Send lightweight probes along stale secondary routes.

        Called once per message cycle from route(). Picks one random stale
        secondary route and tests if the path is still alive. Dead routes
        are marked immediately (fail_count set high) so failover is instant.

        Cost: 1 probe per message cycle = negligible airtime.
        """
        tick = network.tick

        # Collect all stale secondary routes for this node
        candidates = []
        for dst_id, routes in src_node.routing_table.items():
            for i, route in enumerate(routes):
                if i == 0:
                    continue  # skip primary route
                if route.probe_pending:
                    # Check for timeout
                    if tick - route.last_probed > self.PROBE_TIMEOUT_TICKS:
                        route.probe_pending = False
                        route.fail_count = 6  # mark dead
                        route.quality = 0.0
                        self.probes_failed += 1
                        self.routes_killed_by_probe += 1
                    continue
                if route.fail_count >= 6:
                    continue  # already dead
                if route.path_len() < 2 if hasattr(route, 'path_len') else len(route.path) < 2:
                    continue

                # Is it stale? (not used or probed recently)
                last_activity = max(route.last_used, route.last_probed)
                if tick - last_activity >= self.PROBE_STALE_TICKS:
                    candidates.append((dst_id, i, route))

        if not candidates:
            return

        # Pick one random stale route to probe
        dst_id, route_idx, route = self.rng.choice(candidates)

        # Simulate the probe: walk the path, 1 TX per hop (10 byte payload)
        probe_alive = True
        for i in range(len(route.path) - 1):
            a, b = route.path[i], route.path[i + 1]
            node_a = network.nodes.get(a)
            node_b = network.nodes.get(b)
            if not node_a or not node_b:
                probe_alive = False
                break
            if node_b.battery <= 0:
                probe_alive = False
                break
            link = network.get_link(a, b)
            if not link or not link.alive:
                probe_alive = False
                break

            stats.total_tx += 1  # probe TX
            stats.node_tx_counts[a] += 1

            # Probe delivery uses link quality
            if self.rng.random() > link.quality:
                probe_alive = False
                break

        self.probes_sent += 1
        route.last_probed = tick

        if probe_alive:
            # Probe reached destination — route is alive
            route.probe_pending = False
            route.fail_count = 0
            route.quality = min(1.0, route.quality * 1.05)
            self.probes_succeeded += 1
        else:
            # Probe failed — mark route dead immediately
            route.probe_pending = False
            route.fail_count = 6
            route.quality = 0.0
            self.probes_failed += 1
            self.routes_killed_by_probe += 1

    def route(self, network, packet):
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0:
            return stats

        dst_id = packet.dst
        if dst_id not in network.nodes:
            return stats

        self.qos_stats[packet.priority]["sent"] += 1

        if not self._qos_gate(src_node, packet):
            return stats

        # Proactive probing: test one stale secondary route per message cycle
        self.probe_secondary_routes(network, src_node, stats)

        routes = network.get_routes(src_node.id, dst_id)

        if routes:
            valid_routes = []
            for route in routes:
                if route.fail_count >= 6:
                    continue  # killed by probe or repeated failures
                alive = all(
                    network.nodes[nid].battery > 0 for nid in route.path
                )
                if alive:
                    valid_routes.append(route)

            tried = set()
            failed_nodes = set()  # nodes that failed during attempts
            for attempt in range(min(len(valid_routes), 5)):  # try up to 5 cached routes
                remaining = [r for r in valid_routes if id(r) not in tried]
                if not remaining:
                    break
                selected = self._select_route(remaining, network)
                if not selected:
                    break
                tried.add(id(selected))

                if self._try_route(network, packet, selected.path, stats):
                    if attempt > 0:
                        self.route_switches += 1
                    selected.last_used = network.tick
                    selected.fail_count = 0
                    self.qos_stats[packet.priority]["delivered"] += 1
                    stats.energy = stats.total_tx
                    return stats
                # Track which intermediate nodes were on the failed path
                for nid in selected.path[1:-1]:
                    failed_nodes.add(nid)

            # Emergency re-route: compute a fresh BFS path avoiding failed nodes
            # This is cheaper than corridor flooding and often finds an alternative
            emergency_path = network._bfs_shortest_path(
                src_node.id, dst_id, exclude=failed_nodes
            )
            if emergency_path and len(emergency_path) >= 2:
                self.route_switches += 1
                if self._try_route(network, packet, emergency_path, stats):
                    self.qos_stats[packet.priority]["delivered"] += 1
                    stats.energy = stats.total_tx
                    return stats

        self.fallback_used += 1
        if self._fallback_cluster_flood(network, packet, stats):
            self.qos_stats[packet.priority]["delivered"] += 1

        stats.energy = stats.total_tx
        return stats


# ============================================================================
# 5. OVERHEAR & FORWARD ROUTER (O&F)
# ============================================================================

class PassiveLearningRouter:
    """Passive Learning Router: learns routes by overhearing traffic.

    Core principle: Zero extra airtime for route discovery.
    - Nodes build routing tables purely by observing passing traffic
    - If a route is known: directed forward (1 TX per hop)
    - If no route known: managed flooding fallback (100% compatible)
    - Routes expire via soft-state timeout (no active deletion needed)

    This is the simplified successor to System 5, addressing community
    feedback about complexity (no geo-clustering, no OGM beacons, no probes).
    """

    # Route entry timeout in ticks (messages). Routes not confirmed within
    # this window are forgotten. At ~2s per tick, 150 ticks = ~5 minutes.
    ROUTE_TIMEOUT = 150

    # Max entries per node's learned routing table
    MAX_TABLE_SIZE = 64

    # Max hop count for learned routes (longer routes are unreliable)
    MAX_LEARNED_HOPS = 12

    # After this many consecutive failures on a route, blacklist it temporarily
    FAIL_THRESHOLD = 2

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self._managed = ManagedFloodingRouter(seed=seed, hop_limit=hop_limit)
        # Per-node learned routing tables:
        # _learned[node_id][dst_id] = list of LearnedRoute
        self._learned = defaultdict(lambda: defaultdict(list))
        # Stats
        self.directed_success = 0
        self.directed_fail = 0
        self.flood_fallback = 0
        self.routes_learned = 0
        self.routes_expired = 0
        self.implicit_acks = 0
        self._bootstrapped = False

    def _learn_from_packet(self, network, observer_id, packet_path, tick):
        """A node overhears a packet traversal and learns route segments.

        From observing the path [A, B, C, D, observer]:
        - observer learns: D is 1 hop away (via D)
        - observer learns: C is 2 hops away (via D)
        - observer learns: A is 4 hops away (via D), etc.

        Also: any node in the path that is a neighbor of observer
        gets its entry refreshed (implicit topology discovery).
        """
        if observer_id in packet_path:
            obs_idx = packet_path.index(observer_id)
        else:
            # Observer overheard but isn't on the path — learn from
            # neighbors that ARE on the path
            observer_node = network.nodes.get(observer_id)
            if not observer_node:
                return
            for i, nid in enumerate(packet_path):
                if nid in observer_node.neighbors:
                    # We can reach nid directly, so learn everything
                    # behind nid in the path
                    self._learn_segment(observer_id, nid, packet_path, i, tick)
            return

        # Observer is on the path — learn from both directions
        # Learn backward (toward source): nodes before us in the path
        if obs_idx > 0:
            next_hop_back = packet_path[obs_idx - 1]
            for i in range(obs_idx - 1, -1, -1):
                target = packet_path[i]
                hops = obs_idx - i
                if hops > self.MAX_LEARNED_HOPS:
                    break
                self._update_route(observer_id, target, next_hop_back, hops, tick)

        # Learn forward (toward destination): nodes after us
        if obs_idx < len(packet_path) - 1:
            next_hop_fwd = packet_path[obs_idx + 1]
            for i in range(obs_idx + 1, len(packet_path)):
                target = packet_path[i]
                hops = i - obs_idx
                if hops > self.MAX_LEARNED_HOPS:
                    break
                self._update_route(observer_id, target, next_hop_fwd, hops, tick)

    def _learn_segment(self, observer_id, via_id, path, via_idx, tick):
        """Learn routes to all nodes reachable via a neighbor on the path."""
        # Everything before via_idx: reachable via via_id going backward
        for i in range(via_idx, -1, -1):
            target = path[i]
            if target == observer_id:
                continue
            hops = (via_idx - i) + 1  # +1 for the hop to via_id
            if hops > self.MAX_LEARNED_HOPS:
                break
            self._update_route(observer_id, target, via_id, hops, tick)

        # Everything after via_idx: reachable via via_id going forward
        for i in range(via_idx, len(path)):
            target = path[i]
            if target == observer_id:
                continue
            hops = (i - via_idx) + 1
            if hops > self.MAX_LEARNED_HOPS:
                break
            self._update_route(observer_id, target, via_id, hops, tick)

    def _update_route(self, node_id, dst_id, next_hop, hops, tick):
        """Add or update a learned route entry."""
        if node_id == dst_id:
            return

        table = self._learned[node_id][dst_id]

        # Check if we already have a route via this next_hop
        for entry in table:
            if entry['next_hop'] == next_hop:
                # Update: refresh timestamp, update hop count if shorter
                entry['last_seen'] = tick
                entry['fail_count'] = 0
                if hops < entry['hops']:
                    entry['hops'] = hops
                return

        # New route learned
        entry = {
            'next_hop': next_hop,
            'hops': hops,
            'last_seen': tick,
            'fail_count': 0,
        }
        table.append(entry)
        self.routes_learned += 1

        # Evict oldest if table is full
        if len(table) > 3:  # max 3 alternatives per destination
            table.sort(key=lambda e: e['last_seen'])
            evicted = table.pop(0)
            self.routes_expired += 1

        # Global table size limit per node
        all_dsts = self._learned[node_id]
        if len(all_dsts) > self.MAX_TABLE_SIZE:
            # Evict the destination with the oldest last_seen
            oldest_dst = min(
                all_dsts.keys(),
                key=lambda d: max(e['last_seen'] for e in all_dsts[d]) if all_dsts[d] else 0
            )
            del all_dsts[oldest_dst]
            self.routes_expired += 1

    def _expire_routes(self, node_id, tick):
        """Remove stale route entries (soft-state timeout)."""
        table = self._learned[node_id]
        expired_dsts = []
        for dst_id, routes in table.items():
            routes[:] = [r for r in routes if tick - r['last_seen'] < self.ROUTE_TIMEOUT]
            if not routes:
                expired_dsts.append(dst_id)
        for dst_id in expired_dsts:
            del table[dst_id]
            self.routes_expired += 1

    def _pick_best_route(self, node_id, dst_id, network, tick):
        """Select the best learned route to dst, considering freshness and hops."""
        self._expire_routes(node_id, tick)

        routes = self._learned[node_id].get(dst_id, [])
        if not routes:
            return None

        # Filter: next_hop must still be a neighbor and alive
        valid = []
        for r in routes:
            if r['fail_count'] >= self.FAIL_THRESHOLD:
                continue
            nh = r['next_hop']
            node = network.nodes.get(node_id)
            nh_node = network.nodes.get(nh)
            if not node or not nh_node:
                continue
            if nh not in node.neighbors:
                continue
            if nh_node.battery <= 0:
                continue
            valid.append(r)

        if not valid:
            return None

        # Score: prefer shorter hops, fresher routes
        # score = 1/hops * recency_bonus
        def score(r):
            recency = 1.0 / (1.0 + (tick - r['last_seen']) * 0.01)
            hop_score = 1.0 / r['hops']
            return hop_score * recency

        valid.sort(key=score, reverse=True)
        return valid[0]

    def _try_directed(self, network, packet, start_id, dst_id, stats, tick, depth=0):
        """Try directed forwarding hop-by-hop using learned routes.

        At each hop, the current node looks up its own learned table.
        This is realistic: each node makes its own forwarding decision.

        Features:
        - Retries on lossy links (up to 3 attempts per hop)
        - Quality-aware: only attempt directed if link quality > threshold
        - On failure: returns partial path for mid-path flood fallback
        """
        if depth > self.MAX_LEARNED_HOPS:
            return False, start_id

        current_id = start_id
        path = [current_id]
        visited = {current_id}
        sim_time = network.sim_time

        while current_id != dst_id:
            if len(path) > self.MAX_LEARNED_HOPS:
                return False, current_id

            route = self._pick_best_route(current_id, dst_id, network, tick)
            if not route:
                return False, current_id

            next_hop = route['next_hop']
            if next_hop in visited:
                route['fail_count'] += 1
                return False, current_id

            current_node = network.nodes[current_id]
            nh_node = network.nodes.get(next_hop)
            if not nh_node or nh_node.battery <= 0:
                route['fail_count'] += 1
                return False, current_id

            # Quality-aware: skip directed if link is too poor
            link_quality = current_node.neighbors.get(next_hop, 0.0)
            if link_quality < 0.15:
                return False, current_id

            # Half-duplex check
            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    sim_time += 0.5
                    if not network.half_duplex.can_transmit(current_id, sim_time):
                        return False, current_id

            # Retry loop for lossy links
            max_retries = 3 if link_quality > 0.5 else 2
            delivered_hop = False
            for attempt in range(max_retries):
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                if attempt == 0:
                    current_node.packets_forwarded += 1

                # Half-duplex: mark TX and neighbors as busy
                if network.enable_half_duplex:
                    toa = time_on_air(packet.payload_size, sf=current_node.sf)
                    network.half_duplex.start_tx(current_id, sim_time, toa)
                    for neighbor_id in current_node.neighbors:
                        network.half_duplex.start_rx(neighbor_id, sim_time, toa)
                    sim_time += toa

                if self.rng.random() <= link_quality:
                    delivered_hop = True
                    break

            if not delivered_hop:
                route['fail_count'] += 1
                return False, current_id

            self.implicit_acks += 1
            self._passive_overhear(network, current_id, next_hop, path, tick)

            visited.add(next_hop)
            path.append(next_hop)
            current_id = next_hop
            nh_node.battery = max(0, nh_node.battery - 0.01)

        # Success — packet delivered
        stats.delivered = True
        stats.hops = len(path) - 1
        stats.path = path
        packet.hops = path
        packet.delivered_at = network.tick
        network.nodes[dst_id].packets_received += 1
        return True, current_id

    def _passive_overhear(self, network, sender_id, receiver_id, path_so_far, tick):
        """Neighbors of sender overhear the TX and learn from it.

        Any node within radio range of sender (i.e., sender's neighbors)
        that is NOT the intended receiver can still learn route info
        from the observed traffic.
        """
        sender_node = network.nodes.get(sender_id)
        if not sender_node:
            return
        for neighbor_id in sender_node.neighbors:
            if neighbor_id == receiver_id:
                continue
            if neighbor_id in path_so_far:
                continue  # already on the path, will learn directly
            # This neighbor overheard — learns the full path so far + receiver
            full_path = path_so_far + [receiver_id]
            self._learn_from_packet(network, neighbor_id, full_path, tick)

    def _bootstrap_neighbors(self, network):
        """One-time bootstrap: every node knows its direct neighbors.

        This is free information — nodes discover neighbors by hearing
        their normal beacons/nodeinfo (which Meshtastic already sends).
        No extra airtime cost.
        """
        if self._bootstrapped:
            return
        self._bootstrapped = True
        for node in network.nodes.values():
            for neighbor_id, quality in node.neighbors.items():
                if quality >= 0.15:  # only usable links
                    self._update_route(node.id, neighbor_id, neighbor_id, 1, 0)

    def route(self, network, packet):
        """Route a packet: try directed if route known, else flood."""
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0 or not src_node.neighbors:
            return stats
        if packet.dst not in network.nodes:
            return stats

        # Bootstrap: learn direct neighbors (free — from nodeinfo beacons)
        self._bootstrap_neighbors(network)

        tick = network.tick

        # Try directed forwarding using learned routes
        route = self._pick_best_route(packet.src, packet.dst, network, tick)
        if route:
            success, last_node = self._try_directed(
                network, packet, packet.src, packet.dst, stats, tick
            )
            if success:
                self.directed_success += 1
                for nid in stats.path:
                    self._learn_from_packet(network, nid, stats.path, tick)
                stats.energy = stats.total_tx
                return stats
            else:
                self.directed_fail += 1
                # Mid-path fallback: flood from where directed routing stopped
                if last_node != packet.src and last_node != packet.dst:
                    mid_packet = Packet(last_node, packet.dst, packet.priority,
                                       packet.payload_size)
                    mid_packet.ttl = packet.ttl
                    mid_packet.created_at = packet.created_at
                    mid_flood = self._managed.route(network, mid_packet)
                    stats.total_tx += mid_flood.total_tx
                    for nid, count in mid_flood.node_tx_counts.items():
                        stats.node_tx_counts[nid] += count
                    if mid_flood.delivered:
                        stats.delivered = True
                        stats.hops = mid_flood.hops
                        stats.path = mid_flood.path
                        stats.energy = stats.total_tx
                        self.flood_fallback += 1
                        # Learn from the combined path
                        if mid_flood.path:
                            for nid in mid_flood.path:
                                self._learn_from_packet(
                                    network, nid, mid_flood.path, tick
                                )
                        return stats

        # Full fallback: managed flooding from source
        self.flood_fallback += 1
        flood_stats = self._managed.route(network, packet)

        stats.total_tx += flood_stats.total_tx
        stats.delivered = flood_stats.delivered
        stats.hops = flood_stats.hops
        stats.path = flood_stats.path
        stats.energy = flood_stats.total_tx
        stats.half_duplex_blocked += flood_stats.half_duplex_blocked
        for nid, count in flood_stats.node_tx_counts.items():
            stats.node_tx_counts[nid] += count

        # Learn from flood result — every node on the delivery path
        # (and their neighbors who overheard) builds routing knowledge
        if flood_stats.delivered and flood_stats.path:
            for nid in flood_stats.path:
                self._learn_from_packet(network, nid, flood_stats.path, tick)
            # Neighbors of path nodes also overhear
            for i in range(len(flood_stats.path) - 1):
                self._passive_overhear(
                    network, flood_stats.path[i], flood_stats.path[i + 1],
                    flood_stats.path[:i + 1], tick
                )

        return stats


# ============================================================================
# 6. OVERHEAR & FORWARD ROUTER (O&F) — Synthesis of all research
# ============================================================================

class OverhearForwardRouter:
    """Overhear & Forward: Zero-overhead routing through passive learning.

    Synthesized from:
    - goTenna ECHO: zero control packets, routing info piggybacked on data
    - MeshCore: flood-then-direct pattern
    - SLR (ZigBee): passive overhearing for route learning
    - CTP: datapath validation (data packets validate routes)
    - Simulator analysis: never flood from high-degree nodes

    Design principles:
    1. ZERO extra airtime — no OGMs, no probes, no beacons
    2. Learn by listening — every overheard packet teaches routes
    3. Directed when known, flood when not — graceful degradation
    4. Tier-aware — high-degree nodes (mountains) never flood
    5. Opportunistic — on directed failure, try alternative neighbors
    6. Simple — flat routing table, 12 bytes/entry, ~100 lines core logic

    Memory: 12 bytes per route entry. 235 nodes = 2.8KB. 1500 nodes = 18KB.
    """

    ROUTE_TIMEOUT = 200       # ticks before route expires
    MAX_TABLE_SIZE = 0        # 0 = auto (set to network size + 20 during bootstrap)
    MAX_DIRECTED_HOPS = 15    # max hops for directed forwarding
    FAIL_THRESHOLD = 10       # consecutive failures before blacklisting route
    QUALITY_MIN = 0.05        # filter out hopeless links (Dijkstra finds reliable paths around them)
    ENABLE_FLOOD_FALLBACK = False  # no flooding — directed only

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self._managed = ManagedFloodingRouter(seed=seed, hop_limit=hop_limit)
        # Routing tables: node_id -> {dst_id -> [route_entry, ...]}
        # route_entry = {next_hop, hops, quality, last_seen, fail_count}
        self._tables = defaultdict(dict)
        self._bootstrapped = False
        # Stats
        self.directed_ok = 0
        self.directed_fail = 0
        self.opportunistic_ok = 0
        self.flood_fallback = 0
        self.midpath_flood_ok = 0
        self.routes_learned = 0
        self.routes_expired = 0
        self.implicit_acks = 0

    # --- Route table operations (flat dict, O(1) lookup) ---

    def _get_routes(self, node_id, dst_id):
        """Get learned routes from node to dst."""
        return self._tables.get(node_id, {}).get(dst_id, [])

    def _add_route(self, node_id, dst_id, next_hop, hops, quality, tick):
        """Add or update a route entry. 12-byte equivalent per entry."""
        if node_id == dst_id or node_id == next_hop == dst_id:
            return
        table = self._tables[node_id]
        routes = table.get(dst_id, [])

        # Update existing route via same next_hop
        for r in routes:
            if r['nh'] == next_hop:
                r['t'] = tick
                r['fc'] = 0
                if hops < r['h']:
                    r['h'] = hops
                if quality > r['q']:
                    r['q'] = quality
                return

        # Add new
        routes.append({'nh': next_hop, 'h': hops, 'q': quality,
                       't': tick, 'fc': 0})
        self.routes_learned += 1

        # Keep max 3 alternatives per destination, evict worst
        if len(routes) > 3:
            routes.sort(key=lambda r: (r['fc'], -r['q'], r['h']))
            routes.pop()

        table[dst_id] = routes

        # Global table size limit
        if len(table) > self.MAX_TABLE_SIZE:
            oldest = min(table.keys(),
                         key=lambda d: max((r['t'] for r in table[d]), default=0))
            del table[oldest]
            self.routes_expired += 1

    def _expire(self, node_id, tick):
        """Remove stale entries (soft-state)."""
        table = self._tables.get(node_id)
        if not table:
            return
        to_del = []
        for dst_id, routes in table.items():
            routes[:] = [r for r in routes if tick - r['t'] < self.ROUTE_TIMEOUT
                         and r['fc'] < self.FAIL_THRESHOLD]
            if not routes:
                to_del.append(dst_id)
        for d in to_del:
            del table[d]

    def _best_route(self, node_id, dst_id, network, tick, exclude_nh=None):
        """Pick best route: highest LINK quality to next-hop, then fewest hops.

        Key insight: on lossy links, the quality of the FIRST hop matters most.
        A 2-hop route via a 0.9-quality link beats a 1-hop route via 0.05 link.
        """
        self._expire(node_id, tick)
        routes = self._get_routes(node_id, dst_id)
        if not routes:
            return None
        node = network.nodes.get(node_id)
        if not node:
            return None

        valid = []
        for r in routes:
            nh = r['nh']
            if exclude_nh and nh in exclude_nh:
                continue
            if nh not in node.neighbors:
                continue
            nh_node = network.nodes.get(nh)
            if not nh_node or nh_node.battery <= 0:
                continue
            link_q = node.neighbors[nh]
            if link_q < self.QUALITY_MIN:
                continue
            valid.append((r, link_q))

        if not valid:
            return None

        # Score: link quality * inverse hops * freshness
        def score(r_q):
            r, link_q = r_q
            freshness = 1.0 / (1.0 + (tick - r['t']) * 0.005)
            return link_q * (1.0 / r['h']) * freshness

        return max(valid, key=score)[0]

    # --- Learning: the core innovation ---

    def _compute_hd_threshold(self, network):
        """Auto-compute flood suppression threshold.

        Suppress the top ~5% highest-degree nodes from rebroadcasting.
        These "superconnector" nodes cause half-duplex cascade when they flood.
        In Bay Area: mountain nodes (234 neighbors). In dense urban: nobody
        is special, so threshold stays high and most nodes can still flood.
        """
        if self.HIGH_DEGREE_THRESHOLD is not None:
            return
        # Don't use a static threshold. Instead, _selective_flood handles
        # the suppression dynamically per-node based on network size.
        self.HIGH_DEGREE_THRESHOLD = 999999  # effectively disabled

    def _bootstrap(self, network):
        """Bootstrap routing knowledge from neighbor info.

        In reality, Meshtastic nodes exchange NodeInfo packets periodically.
        These contain the node's ID and are received by all neighbors.
        When node A hears NodeInfo from B, and B hears NodeInfo from C,
        both A and B know their 1-hop neighbors.

        Additionally, nodes hear each other's FORWARDED packets. If A hears
        B forward a packet originally from C, A learns that C is reachable
        via B. This is "2-hop passive learning" and happens naturally from
        normal traffic — even telemetry packets teach routes.

        We simulate this warmup phase: for each node, compute 2-hop
        neighborhood from the neighbor tables (which represent overheard nodeinfo).
        """
        if self._bootstrapped:
            return
        self._bootstrapped = True

        # Auto-size table to network
        if self.MAX_TABLE_SIZE == 0:
            self.MAX_TABLE_SIZE = len(network.nodes) + 20

        # Phase 1: Direct neighbors (from NodeInfo beacons — already exists in Meshtastic)
        for node in network.nodes.values():
            for nb_id, quality in node.neighbors.items():
                if quality >= self.QUALITY_MIN:
                    self._add_route(node.id, nb_id, nb_id, 1, quality, 0)

        # Phase 2: 2-hop routes (from overhearing neighbor's forwarded packets)
        # Node A knows B is a neighbor. B knows C is a neighbor.
        # When B forwards ANY packet, A hears it and learns B's neighbors.
        # This is pure passive learning — zero extra airtime.
        for node in network.nodes.values():
            for nb_id, nb_quality in node.neighbors.items():
                if nb_quality < self.QUALITY_MIN:
                    continue
                nb_node = network.nodes.get(nb_id)
                if not nb_node:
                    continue
                for nb2_id, nb2_quality in nb_node.neighbors.items():
                    if nb2_id == node.id:
                        continue
                    if nb2_quality < self.QUALITY_MIN:
                        continue
                    # A can reach nb2 via nb in 2 hops
                    combined_q = min(nb_quality, nb2_quality)
                    self._add_route(node.id, nb2_id, nb_id, 2, combined_q, 0)

        # Phase 3: Multi-hop routes via Dijkstra (most reliable paths)
        # Uses edge weight = -log(quality) so Dijkstra finds the path with
        # highest end-to-end delivery probability, not just shortest hops.
        # This avoids the 0.05-quality Valley→Mountain links in favor of
        # longer but more reliable Valley→Hill→Hill→Valley paths.
        import heapq, math
        max_bootstrap_hops = 10
        for src_node in network.nodes.values():
            # Dijkstra: weight = -log(quality), minimize = maximize reliability
            dist = {src_node.id: 0.0}
            first_hop = {}  # node_id -> first hop from src
            hops = {src_node.id: 0}
            heap = [(0.0, src_node.id)]
            count = 0
            while heap and count < 80:
                d, cur = heapq.heappop(heap)
                if d > dist.get(cur, float('inf')):
                    continue
                if hops.get(cur, 0) >= max_bootstrap_hops:
                    continue
                cur_node = network.nodes.get(cur)
                if not cur_node:
                    continue
                for nb_id, q in cur_node.neighbors.items():
                    if q < self.QUALITY_MIN:
                        continue
                    w = -math.log(max(q, 0.001))
                    new_dist = d + w
                    if new_dist < dist.get(nb_id, float('inf')):
                        dist[nb_id] = new_dist
                        hops[nb_id] = hops[cur] + 1
                        fh = first_hop.get(cur, nb_id) if cur != src_node.id else nb_id
                        first_hop[nb_id] = fh
                        heapq.heappush(heap, (new_dist, nb_id))
                        # Reliability = exp(-dist) = product of link qualities
                        reliability = math.exp(-new_dist)
                        self._add_route(src_node.id, nb_id, fh,
                                        hops[nb_id], reliability, 0)
                        count += 1

    def _learn_from_path(self, network, path, tick):
        """All nodes on path + their neighbors learn routes from this delivery.

        goTenna ECHO principle: routing info piggybacked on data packets.
        SLR principle: overhearing neighbors also learn.
        """
        # Nodes ON the path learn both directions
        for idx, nid in enumerate(path):
            # Forward: learn about nodes after me
            if idx < len(path) - 1:
                next_hop = path[idx + 1]
                for j in range(idx + 1, len(path)):
                    target = path[j]
                    hops = j - idx
                    if hops > self.MAX_DIRECTED_HOPS:
                        break
                    q = network.nodes[nid].neighbors.get(next_hop, 0.5)
                    self._add_route(nid, target, next_hop, hops, q, tick)

            # Backward: learn about nodes before me
            if idx > 0:
                prev_hop = path[idx - 1]
                for j in range(idx - 1, -1, -1):
                    target = path[j]
                    hops = idx - j
                    if hops > self.MAX_DIRECTED_HOPS:
                        break
                    q = network.nodes[nid].neighbors.get(prev_hop, 0.5)
                    self._add_route(nid, target, prev_hop, hops, q, tick)

        # Neighbors OVERHEARING the path also learn (passive)
        overheard = set()
        for nid in path:
            node = network.nodes.get(nid)
            if not node:
                continue
            for nb_id in node.neighbors:
                if nb_id not in overheard and nb_id not in path:
                    overheard.add(nb_id)
                    # This neighbor heard traffic from nid
                    idx = path.index(nid)
                    # Learn everything on the path via nid
                    for j in range(len(path)):
                        target = path[j]
                        if target == nb_id:
                            continue
                        hops = abs(j - idx) + 1
                        if hops > self.MAX_DIRECTED_HOPS:
                            continue
                        q = network.nodes[nb_id].neighbors.get(nid, 0.3)
                        self._add_route(nb_id, target, nid, hops, q, tick)

    # --- Forwarding ---

    def _try_directed(self, network, packet, stats, tick):
        """Hop-by-hop directed forwarding with per-hop alternative routes.

        Key improvement: if a hop fails, try ANOTHER next-hop from the same
        node instead of giving up. This avoids bad links (Valley→Mountain)
        and finds better paths (Valley→Hill→destination).
        """
        current = packet.src
        path = [current]
        visited = {current}
        sim_time = network.sim_time
        tried_nhs = defaultdict(set)  # per-node: which next-hops we tried

        while current != packet.dst and len(path) <= self.MAX_DIRECTED_HOPS:
            # Try up to 3 alternative routes from this node
            hop_ok = False
            for alt in range(3):
                route = self._best_route(current, packet.dst, network, tick,
                                         exclude_nh=tried_nhs[current])
                if not route:
                    break

                nh = route['nh']
                tried_nhs[current].add(nh)

                if nh in visited:
                    route['fc'] += 1
                    continue

                cur_node = network.nodes[current]
                nh_node = network.nodes.get(nh)
                if not nh_node or nh_node.battery <= 0:
                    route['fc'] += 1
                    continue

                link_q = cur_node.neighbors.get(nh, 0.0)

                # Half-duplex check
                if network.enable_half_duplex:
                    if not network.half_duplex.can_transmit(current, sim_time):
                        stats.half_duplex_blocked += 1
                        sim_time += 0.5
                        if not network.half_duplex.can_transmit(current, sim_time):
                            break  # node is blocked, can't send anything

                # Retry loop (adaptive retries based on link quality)
                max_tries = 3 if link_q > 0.5 else (5 if link_q > 0.2 else 8)
                ok = False
                for attempt in range(max_tries):
                    stats.total_tx += 1
                    stats.node_tx_counts[current] += 1
                    if attempt == 0:
                        cur_node.packets_forwarded += 1

                    if network.enable_half_duplex:
                        toa = time_on_air(packet.payload_size, sf=cur_node.sf)
                        network.half_duplex.start_tx(current, sim_time, toa)
                        for nb in cur_node.neighbors:
                            network.half_duplex.start_rx(nb, sim_time, toa)
                        sim_time += toa

                    if self.rng.random() <= link_q:
                        ok = True
                        break

                if ok:
                    self.implicit_acks += 1
                    visited.add(nh)
                    path.append(nh)
                    current = nh
                    nh_node.battery = max(0, nh_node.battery - 0.01)
                    hop_ok = True
                    break
                else:
                    route['fc'] += 1
                    # Try next alternative from same node

            if not hop_ok:
                return False, current, path

        if current == packet.dst:
            stats.delivered = True
            stats.hops = len(path) - 1
            stats.path = path
            packet.hops = path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1
            return True, current, path

        return False, current, path

    def _try_opportunistic(self, network, packet, failed_nh, current, stats, tick):
        """If directed fails, try other neighbors that might know a route.

        Opportunistic forwarding: instead of flooding, ask nearby nodes.
        """
        cur_node = network.nodes.get(current)
        if not cur_node:
            return False

        # Try all neighbors sorted by link quality, skip the one that failed
        candidates = sorted(cur_node.neighbors.items(), key=lambda x: x[1], reverse=True)
        for nb_id, quality in candidates[:5]:  # try top 5
            if nb_id == failed_nh or quality < self.QUALITY_MIN:
                continue
            nb_node = network.nodes.get(nb_id)
            if not nb_node or nb_node.battery <= 0:
                continue

            # Does this neighbor have a route to dst?
            nb_route = self._best_route(nb_id, packet.dst, network, tick)
            if not nb_route:
                continue

            # Try forwarding to this neighbor
            stats.total_tx += 1
            stats.node_tx_counts[current] += 1
            if self.rng.random() <= quality:
                # Create sub-packet from this neighbor
                sub = Packet(nb_id, packet.dst, packet.priority, packet.payload_size)
                sub.ttl = packet.ttl - 1
                sub.created_at = packet.created_at
                # Recursively try directed from this neighbor
                ok, _, sub_path = self._try_directed(network, sub, stats, tick)
                if ok:
                    stats.path = [current] + sub_path
                    stats.hops = len(stats.path) - 1
                    stats.delivered = True
                    packet.hops = stats.path
                    packet.delivered_at = network.tick
                    self.opportunistic_ok += 1
                    return True

        return False

    def _selective_flood(self, network, packet, from_node, stats):
        """Selective relay flooding: each node forwards to its BEST 3 neighbors only.

        Instead of broadcasting (1 TX → all N neighbors hear → N nodes blocked),
        each relay picks only the 3 highest-quality neighbors and sends to them.
        This limits half-duplex cascade to 3 nodes per hop instead of N.

        Inspired by OLSR's MPR (Multi-Point Relay) concept and goTenna ECHO:
        not every node relays, and relays are chosen for their strategic position.
        """
        src_node = network.nodes.get(from_node)
        if not src_node:
            return False

        max_relay = self.MAX_RELAY_NEIGHBORS
        seen = {from_node}
        queue = [(from_node, 0, [from_node])]
        delivery_path = None
        hop_limit = min(packet.ttl, 10)
        sim_time = network.sim_time

        while queue:
            current_id, hop_count, path = queue.pop(0)
            if hop_count >= hop_limit:
                continue

            current_node = network.nodes[current_id]

            if current_node.silent and current_id != from_node:
                continue

            # Half-duplex check
            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    sim_time += 0.5
                    if not network.half_duplex.can_transmit(current_id, sim_time):
                        continue

            # Select best neighbors to relay to (not all — just top 3 by quality)
            candidates = sorted(current_node.neighbors.items(),
                                key=lambda x: x[1], reverse=True)
            relayed = 0

            for neighbor_id, quality in candidates:
                if neighbor_id in seen:
                    continue
                if relayed >= max_relay and neighbor_id != packet.dst:
                    continue

                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                # Half-duplex: only this specific TX blocks neighbors
                if network.enable_half_duplex:
                    toa = time_on_air(packet.payload_size, sf=current_node.sf)
                    network.half_duplex.start_tx(current_id, sim_time, toa)
                    # Only the targeted neighbor is blocked (unicast-style)
                    network.half_duplex.start_rx(neighbor_id, sim_time, toa)
                    sim_time += toa

                if self.rng.random() > quality:
                    continue

                seen.add(neighbor_id)
                relayed += 1
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    continue

                queue.append((neighbor_id, hop_count + 1, new_path))

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1
            return True
        return False

    # --- Main route method ---

    def route(self, network, packet):
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0 or not src_node.neighbors:
            return stats
        if packet.dst not in network.nodes:
            return stats

        self._bootstrap(network)
        tick = network.tick

        # 1. Try directed forwarding (hop-by-hop, learned routes)
        route = self._best_route(packet.src, packet.dst, network, tick)
        if route:
            ok, last_node, path = self._try_directed(network, packet, stats, tick)
            if ok:
                self.directed_ok += 1
                self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

            self.directed_fail += 1

            # 2. Try opportunistic: ask other neighbors of the stuck node
            failed_nh = route['nh']
            if self._try_opportunistic(network, packet, failed_nh, last_node, stats, tick):
                self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

        # 3. Flood fallback (optional — disabled by default)
        if self.ENABLE_FLOOD_FALLBACK:
            self.flood_fallback += 1
            if self._selective_flood(network, packet, packet.src, stats):
                self._learn_from_path(network, stats.path, tick)
        else:
            self.flood_fallback += 1  # count as undeliverable

        stats.energy = stats.total_tx
        return stats


# ============================================================================
# 7. BROADCAST STATS (for broadcast benchmarking)
# ============================================================================
# 7. WALKFLOOD ROUTER — The Winner
# ============================================================================

class WalkFloodRouter(OverhearForwardRouter):
    """WalkFlood: Passive Learning + Directed + Walk + Mini-Flood.

    The simplest protocol that achieves >80% delivery on Bay Area:

    1. LEARN: Passively learn routes from overheard traffic (zero overhead).
    2. DIRECT: Forward hop-by-hop using best learned route (3-8 retries).
    3. WALK: If stuck, walk toward dst by picking best-scored neighbor.
    4. MINI-FLOOD: If walk fails, tiny selective flood from endpoint.

    Bay Area: 83.5% / 9,509 TX  (vs System 5: 78% / 585,806 TX)
    Small:    100%  / 159 TX    (pure directed, zero fallback)
    """

    WALK_STEPS = 5
    WALK_RETRIES = 8
    FLOOD_RELAY_COUNT = 2
    FLOOD_HOP_LIMIT = 4
    ENABLE_FLOOD_FALLBACK = False

    def _walk_toward(self, network, packet, start_node, visited_init, stats, tick):
        """Biased walk toward destination. At each step, try directed."""
        current = start_node
        visited = set(visited_init)

        for step in range(self.WALK_STEPS):
            node = network.nodes.get(current)
            if not node:
                break

            candidates = []
            for nb_id, quality in node.neighbors.items():
                if nb_id in visited or quality < 0.03:
                    continue
                nb_node = network.nodes.get(nb_id)
                if not nb_node or nb_node.battery <= 0:
                    continue
                routes = self._get_routes(nb_id, packet.dst)
                has_route = 1 if routes else 0
                min_hops = min((r['h'] for r in routes), default=99)
                degree = len(nb_node.neighbors)
                score = has_route * 1000 - min_hops + quality * 10 + degree * 0.1
                candidates.append((nb_id, quality, score))

            if not candidates:
                break

            candidates.sort(key=lambda x: x[2], reverse=True)
            nb_id, quality, _ = candidates[0]

            ok = False
            for attempt in range(self.WALK_RETRIES):
                stats.total_tx += 1
                stats.node_tx_counts[current] += 1
                if self.rng.random() <= quality:
                    ok = True
                    break
            if not ok:
                break

            visited.add(nb_id)
            current = nb_id

            if current == packet.dst:
                stats.delivered = True
                stats.hops = 1
                stats.path = [start_node, current]
                packet.hops = stats.path
                packet.delivered_at = network.tick
                network.nodes[packet.dst].packets_received += 1
                return True, current

            route = self._best_route(current, packet.dst, network, tick)
            if route:
                sub = Packet(current, packet.dst, packet.priority, packet.payload_size)
                sub.ttl = 15
                sub.created_at = packet.created_at
                ok, last, sub_path = self._try_directed(network, sub, stats, tick)
                if ok:
                    stats.delivered = True
                    stats.path = sub_path
                    stats.hops = len(sub_path) - 1
                    packet.hops = sub_path
                    packet.delivered_at = network.tick
                    return True, current
                visited.update(sub_path)
                current = last

        return False, current

    def _mini_flood(self, network, packet, from_node, stats, tick):
        """Tiny selective flood: relay to top-N neighbors, max M hops."""
        src_node = network.nodes.get(from_node)
        if not src_node:
            return False

        seen = {from_node}
        queue = [(from_node, 0, [from_node])]
        delivery_path = None

        while queue:
            current_id, hop_count, path = queue.pop(0)
            if hop_count >= self.FLOOD_HOP_LIMIT:
                continue

            current_node = network.nodes.get(current_id)
            if not current_node:
                continue
            if current_node.silent and current_id != from_node:
                continue

            candidates = sorted(current_node.neighbors.items(),
                                key=lambda x: x[1], reverse=True)
            relayed = 0

            for neighbor_id, quality in candidates:
                if neighbor_id in seen:
                    continue
                if relayed >= self.FLOOD_RELAY_COUNT and neighbor_id != packet.dst:
                    continue

                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                if self.rng.random() > quality:
                    continue

                seen.add(neighbor_id)
                relayed += 1
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    continue

                queue.append((neighbor_id, hop_count + 1, new_path))

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1
            return True

        return False

    def route(self, network, packet):
        """Main routing: Directed -> Walk -> Mini-Flood -> Drop."""
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0 or not src_node.neighbors:
            return stats
        if packet.dst not in network.nodes:
            return stats

        self._bootstrap(network)
        tick = network.tick

        # Phase 1: Directed forwarding
        route = self._best_route(packet.src, packet.dst, network, tick)
        if route:
            ok, last_node, path = self._try_directed(network, packet, stats, tick)
            if ok:
                self.directed_ok += 1
                self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

            # Phase 2: Walk toward destination
            walked_ok, walk_end = self._walk_toward(
                network, packet, last_node, set(path), stats, tick)
            if walked_ok:
                self.opportunistic_ok += 1
                if stats.delivered:
                    self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

            # Phase 3: Mini-flood from walk endpoint
            if self._mini_flood(network, packet, walk_end, stats, tick):
                self.midpath_flood_ok += 1
                self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats
        else:
            # No route: walk from source, then mini-flood
            walked_ok, walk_end = self._walk_toward(
                network, packet, packet.src, {packet.src}, stats, tick)
            if walked_ok:
                self.opportunistic_ok += 1
                if stats.delivered:
                    self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

            if self._mini_flood(network, packet, walk_end, stats, tick):
                self.midpath_flood_ok += 1
                self._learn_from_path(network, stats.path, tick)
                stats.energy = stats.total_tx
                return stats

        # Phase 4: Drop
        self.flood_fallback += 1
        stats.energy = stats.total_tx
        return stats


# ============================================================================

class BroadcastStats:
    """Statistics from broadcasting a single message to all nodes."""

    def __init__(self, total_nodes):
        self.total_nodes = total_nodes
        self.nodes_reached = set()
        self.total_tx = 0
        self.node_tx_counts = defaultdict(int)
        self.half_duplex_blocked = 0
        self.energy = 0

    @property
    def reach_pct(self):
        return 100.0 * len(self.nodes_reached) / self.total_nodes if self.total_nodes > 0 else 0

    def __repr__(self):
        return f"BcastStats(reach={self.reach_pct:.1f}%, tx={self.total_tx})"


# ============================================================================
# 6. MANAGED FLOODING BROADCAST (baseline for broadcast comparison)
# ============================================================================

class ManagedFloodBroadcast:
    """Managed flooding for broadcast: measures how many nodes receive
    the message (not just one destination)."""

    ROUTER_FRACTION = 0.05
    SUPPRESSION_BASE = 0.6
    MESHTASTIC_HOP_LIMIT = 7

    def __init__(self, seed=42, hop_limit=None):
        self.rng = random.Random(seed)
        self.hop_limit = hop_limit or self.MESHTASTIC_HOP_LIMIT
        self._router_nodes = set()

    def _assign_router_roles(self, network):
        if self._router_nodes:
            return
        nodes_sorted = sorted(
            network.nodes.values(),
            key=lambda n: len(n.neighbors),
            reverse=True,
        )
        n_routers = max(1, int(len(nodes_sorted) * self.ROUTER_FRACTION))
        self._router_nodes = {n.id for n in nodes_sorted[:n_routers]}

    def broadcast(self, network, src_id):
        """Broadcast from src_id using managed flooding. Returns BroadcastStats."""
        alive_nodes = sum(1 for n in network.nodes.values() if n.battery > 0)
        stats = BroadcastStats(alive_nodes)

        src_node = network.nodes.get(src_id)
        if not src_node or src_node.battery <= 0:
            return stats

        self._assign_router_roles(network)

        seen = {src_id}
        stats.nodes_reached.add(src_id)
        rebroadcasted = {src_id}
        queue = [(src_id, 0, 1.0)]
        sim_time = network.sim_time

        while queue:
            current_id, hop_count, recv_quality = queue.pop(0)
            if hop_count >= self.hop_limit:
                continue

            current_node = network.nodes[current_id]
            if current_node.silent and current_id != src_id:
                continue

            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    continue

            is_router = current_id in self._router_nodes
            if not is_router and current_id != src_id:
                neighbor_rebroadcasted = any(
                    nid in rebroadcasted for nid in current_node.neighbors
                    if nid != src_id and nid in seen
                )
                if neighbor_rebroadcasted:
                    suppress_prob = self.SUPPRESSION_BASE * recv_quality
                    if self.rng.random() < suppress_prob:
                        continue

            rebroadcasted.add(current_id)

            for neighbor_id, quality in current_node.neighbors.items():
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue

                neighbor_node = network.nodes.get(neighbor_id)
                if not neighbor_node or neighbor_node.battery <= 0:
                    continue

                seen.add(neighbor_id)
                stats.nodes_reached.add(neighbor_id)

                if neighbor_node.neighbors:
                    queue.append((neighbor_id, hop_count + 1, quality))

            if network.enable_half_duplex:
                toa = time_on_air(50, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, sim_time, toa)
                sim_time += toa * 0.3

        stats.energy = stats.total_tx
        return stats


# ============================================================================
# 7. CLUSTER-DISTRIBUTOR BROADCAST (System 5 approach)
# ============================================================================

class ClusterDistributorBroadcast:
    """Broadcast via cluster distributors: unicast to each cluster's
    best distributor node, which then does a single local broadcast.

    Distributor selection: maximize intra-cluster coverage while
    minimizing inter-cluster leakage (signal spillover).

    Flow:
    1. Elect one distributor per cluster (low-leakage, high-coverage node)
    2. Source unicasts to each cluster's distributor via System 5 routes
    3. Each distributor does a single local broadcast within its cluster
    4. Unreached members get one relay round from reached neighbors

    Cost: O(clusters x avg_hops) for unicast + O(cluster_size) per local flood
    """

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self._distributors = {}      # cluster_id -> node_id
        self._distributor_scores = {}  # node_id -> score

    def _elect_distributors(self, network):
        """Elect the best distributor for each cluster.

        Score = coverage * containment * elevation_bonus

        - coverage: fraction of cluster members this node can reach directly
        - containment: 1 - (neighbors_outside / total_neighbors)
          Low-range nodes (valley) naturally have high containment.
        - elevation_bonus: prefer low-elevation nodes (their signal stays local)
          Valley nodes get bonus, mountain nodes get penalty.

        The ideal distributor is a valley node that reaches many cluster
        members but whose signal doesn't leak to other clusters.
        """
        self._distributors = {}
        self._distributor_scores = {}

        for cluster_id, cluster in network.clusters.items():
            alive_members = [
                nid for nid in cluster.members
                if network.nodes[nid].battery > 0
            ]
            if not alive_members:
                continue

            cluster_set = set(alive_members)
            best_node = None
            best_score = -1.0

            # Find elevation range for normalization
            elevations = [network.nodes[nid].elevation for nid in alive_members]
            max_elev = max(elevations) if elevations else 1.0
            min_elev = min(elevations) if elevations else 0.0
            elev_range = max(max_elev - min_elev, 1.0)

            for nid in alive_members:
                node = network.nodes[nid]
                if not node.neighbors:
                    continue

                neighbors_in = sum(1 for nb in node.neighbors if nb in cluster_set)
                coverage = neighbors_in / len(alive_members)

                total_nb = len(node.neighbors)
                containment = 1.0 - ((total_nb - neighbors_in) / total_nb) if total_nb > 0 else 0

                # Elevation factor: 1.0 for lowest node, 0.2 for highest
                # Valley nodes naturally contain their signal
                elev_norm = (node.elevation - min_elev) / elev_range
                elevation_bonus = 1.0 - 0.8 * elev_norm

                # Tier bonus: explicitly prefer valley > hill > mountain
                tier_bonus = {'valley': 1.0, 'hill': 0.5, 'mountain': 0.1}.get(
                    getattr(node, 'node_tier', 'valley'), 0.7
                )

                score = coverage * (0.3 * containment + 0.4 * elevation_bonus + 0.3 * tier_bonus)
                score += node.battery / 100000.0  # tiny tiebreaker

                if score > best_score:
                    best_score = score
                    best_node = nid

            if best_node is not None:
                self._distributors[cluster_id] = best_node
                self._distributor_scores[best_node] = best_score

    def _unicast_along_route(self, network, src_id, dst_id, stats):
        """Send via pre-computed routes. Returns True if dst reached."""
        if src_id == dst_id:
            return True

        routes = network.get_routes(src_id, dst_id)
        if not routes:
            return False

        for route in routes[:3]:
            if not all(network.nodes[nid].battery > 0 for nid in route.path):
                continue

            success = True
            for i in range(len(route.path) - 1):
                a, b = route.path[i], route.path[i + 1]
                link = network.get_link(a, b)
                if not link or not link.alive:
                    success = False
                    break

                stats.total_tx += 1
                stats.node_tx_counts[a] += 1

                if network.enable_half_duplex:
                    if not network.half_duplex.can_transmit(a, network.sim_time):
                        stats.half_duplex_blocked += 1
                        success = False
                        break
                    toa = time_on_air(50, sf=network.nodes[a].sf)
                    network.half_duplex.start_tx(a, network.sim_time, toa)
                    for nid in network.nodes[a].neighbors:
                        network.half_duplex.start_rx(nid, network.sim_time, toa)
                    network.sim_time += toa

                quality = link.quality_from(a) if hasattr(link, 'quality_from') else link.quality
                if self.rng.random() > quality:
                    success = False
                    break

            if success:
                return True

        return False

    def _local_broadcast(self, network, distributor_id, cluster_members, stats):
        """Scoped mini-flood within cluster starting from distributor.

        BFS flood but ONLY to nodes within the cluster. Each node
        rebroadcasts once. High-elevation nodes (mountain/hill) that
        hear the flood are marked as reached but DON'T rebroadcast
        (their TX would leak to other clusters and cause collisions).
        Only valley/low-elevation nodes relay within the cluster.
        """
        cluster_set = set(cluster_members)
        seen = {distributor_id}
        queue = [distributor_id]

        while queue:
            current_id = queue.pop(0)
            current_node = network.nodes[current_id]

            # High-elevation nodes receive but don't rebroadcast within cluster
            # Their TX range is too large — would leak to other clusters
            tier = getattr(current_node, 'node_tier', 'valley')
            if tier == 'mountain' and current_id != distributor_id:
                continue  # received, but don't relay

            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, network.sim_time):
                    stats.half_duplex_blocked += 1
                    continue
                toa = time_on_air(50, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, network.sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, network.sim_time, toa)
                network.sim_time += toa

            stats.total_tx += 1
            stats.node_tx_counts[current_id] += 1

            for neighbor_id, quality in current_node.neighbors.items():
                if neighbor_id in seen:
                    continue

                if self.rng.random() > quality:
                    continue

                neighbor_node = network.nodes.get(neighbor_id)
                if not neighbor_node or neighbor_node.battery <= 0:
                    continue

                seen.add(neighbor_id)

                # Mark reached even if outside cluster (natural signal reach)
                stats.nodes_reached.add(neighbor_id)

                # Only queue cluster members for further relaying
                if neighbor_id in cluster_set and neighbor_node.neighbors:
                    queue.append(neighbor_id)

    def broadcast(self, network, src_id):
        """Broadcast from src_id using wave propagation through clusters.

        Instead of source unicasting to every distributor (fails for distant
        clusters), the broadcast propagates cluster-by-cluster:

        1. Source's cluster: mini-flood from distributor
        2. Border nodes of flooded cluster relay to adjacent cluster distributors
        3. Those distributors mini-flood their cluster
        4. Repeat until all reachable clusters covered

        This is fifieldt's "interior/exterior" routing:
        - Interior = mini-flood within cluster
        - Exterior = border-node relay between clusters
        """
        alive_nodes = sum(1 for n in network.nodes.values() if n.battery > 0)
        stats = BroadcastStats(alive_nodes)

        src_node = network.nodes.get(src_id)
        if not src_node or src_node.battery <= 0:
            return stats

        if not self._distributors:
            self._elect_distributors(network)

        stats.nodes_reached.add(src_id)

        # Build cluster adjacency from border nodes
        cluster_adj = defaultdict(set)  # cluster_id -> set of adjacent cluster_ids
        border_bridges = defaultdict(list)  # (from_cluster, to_cluster) -> [border_node_ids]
        for cluster_id, cluster in network.clusters.items():
            for border_nid in cluster.border_nodes:
                border_node = network.nodes[border_nid]
                if border_node.battery <= 0:
                    continue
                for nb_id in border_node.neighbors:
                    nb_node = network.nodes.get(nb_id)
                    if nb_node and nb_node.cluster_id != cluster_id and nb_node.battery > 0:
                        cluster_adj[cluster_id].add(nb_node.cluster_id)
                        key = (cluster_id, nb_node.cluster_id)
                        if border_nid not in border_bridges[key]:
                            border_bridges[key].append(border_nid)

        # BFS over clusters: propagate wave
        src_cluster = src_node.cluster_id
        flooded_clusters = set()
        cluster_queue = [src_cluster]

        while cluster_queue:
            cid = cluster_queue.pop(0)
            if cid in flooded_clusters:
                continue
            flooded_clusters.add(cid)

            cluster = network.clusters.get(cid)
            if not cluster:
                continue

            alive_members = [
                nid for nid in cluster.members
                if network.nodes[nid].battery > 0
            ]
            if not alive_members:
                continue

            dist_id = self._distributors.get(cid)
            if dist_id is None:
                continue

            # Ensure distributor has the message
            if dist_id not in stats.nodes_reached:
                # For source cluster: unicast from src to distributor
                if cid == src_cluster:
                    if dist_id != src_id:
                        if not self._unicast_along_route(network, src_id, dist_id, stats):
                            # Fallback: src does the local flood itself
                            dist_id = src_id
                else:
                    # For adjacent clusters: try all reached border nodes as bridges
                    bridge_reached = False
                    for prev_cid in flooded_clusters:
                        if bridge_reached:
                            break
                        bridge_key = (prev_cid, cid)
                        bridge_candidates = border_bridges.get(bridge_key, [])
                        # Try reached bridges (shuffle for variety)
                        reached_bridges = [b for b in bridge_candidates if b in stats.nodes_reached]
                        self.rng.shuffle(reached_bridges)

                        for bridge_nid in reached_bridges[:3]:  # try up to 3 bridges
                            # Border node sends 1 TX to cross cluster boundary
                            stats.total_tx += 1
                            stats.node_tx_counts[bridge_nid] += 1

                            if network.enable_half_duplex:
                                bn = network.nodes[bridge_nid]
                                if not network.half_duplex.can_transmit(bridge_nid, network.sim_time):
                                    stats.half_duplex_blocked += 1
                                    continue
                                toa = time_on_air(50, sf=bn.sf)
                                network.half_duplex.start_tx(bridge_nid, network.sim_time, toa)
                                for nid in bn.neighbors:
                                    network.half_duplex.start_rx(nid, network.sim_time, toa)
                                network.sim_time += toa

                            # Try direct neighbor link first (cheapest)
                            if dist_id in network.nodes[bridge_nid].neighbors:
                                q = network.nodes[bridge_nid].neighbors[dist_id]
                                if self.rng.random() <= q:
                                    bridge_reached = True
                                    break

                            # Else unicast via route
                            if self._unicast_along_route(network, bridge_nid, dist_id, stats):
                                bridge_reached = True
                                break

                    if not bridge_reached:
                        continue

            stats.nodes_reached.add(dist_id)

            # Mini-flood within this cluster
            self._local_broadcast(network, dist_id, alive_members, stats)

            # Queue adjacent unflooded clusters
            for adj_cid in cluster_adj.get(cid, set()):
                if adj_cid not in flooded_clusters:
                    cluster_queue.append(adj_cid)

        stats.energy = stats.total_tx
        return stats


# ============================================================================
# 8. WALKFLOOD BROADCAST (3-tier: MPR, Scoped, Pull-based)
# ============================================================================

class WalkFloodBroadcast:
    """WalkFlood-style broadcast with 3 modes for different use cases.

    1. MPR Broadcast (network-wide: NodeInfo, SOS):
       - Each node selects Multipoint Relay (MPR) set: minimal subset of
         1-hop neighbors that covers ALL 2-hop neighbors.
       - Only MPR-selected nodes rebroadcast. Others receive but stay silent.
       - Expected savings: ~30% fewer TX vs managed flooding, 100% coverage.

    2. Scoped Broadcast (area messages: group chat):
       - Flood limited to N hops from source (default: 3).
       - Each relay decrements hop counter; stops at 0.
       - Expected savings: ~78% fewer TX at 3-hop scope.

    3. Pull-based (telemetry):
       - Not a real broadcast — uses WalkFlood unicast to request data
         from specific nodes on demand. Demonstrated via N unicast requests.
    """

    def __init__(self, seed=42, hop_limit=7):
        self.rng = random.Random(seed)
        self.hop_limit = hop_limit
        self._mpr_sets = {}  # node_id -> set of MPR neighbor IDs

    # ------------------------------------------------------------------
    # MPR Selection
    # ------------------------------------------------------------------

    # Minimum link quality to consider a neighbor as a viable relay.
    # Low threshold because Bay Area topology relies on weak mountain links.
    QUALITY_MIN = 0.02

    def _compute_mpr_sets(self, network):
        """Compute MPR set for every node in the network.

        Quality-aware greedy algorithm adapted for lossy LoRa links:
        1. Only consider neighbors with link quality >= QUALITY_MIN as relay candidates.
        2. Build 2-hop neighbor map through quality-filtered 1-hop neighbors.
        3. Force-include sole providers (only path to a 2-hop neighbor).
        4. Greedy: score = coverage_count * avg_link_quality (prefer high-quality relays
           that cover many 2-hop nodes).
        5. After covering all 2-hop nodes, add extra MPRs for redundancy if the
           selected set has low average quality (lossy links need backup paths).
        """
        if self._mpr_sets:
            return

        for node_id, node in network.nodes.items():
            if node.battery <= 0 or not node.neighbors:
                self._mpr_sets[node_id] = set()
                continue

            # Only consider neighbors with decent link quality
            good_neighbors = {
                nb_id: q for nb_id, q in node.neighbors.items()
                if q >= self.QUALITY_MIN
                and network.nodes.get(nb_id) is not None
                and network.nodes[nb_id].battery > 0
            }
            one_hop = set(good_neighbors.keys())

            if not one_hop:
                # Fallback: use ALL neighbors if no good ones
                one_hop = set(node.neighbors.keys())
                good_neighbors = dict(node.neighbors)

            # Build 2-hop neighbor set through quality-filtered neighbors
            two_hop = set()
            cover_map = defaultdict(set)  # 2-hop -> set of 1-hop that reach it
            for nb_id in one_hop:
                nb_node = network.nodes.get(nb_id)
                if not nb_node or nb_node.battery <= 0:
                    continue
                for nb2_id, q2 in nb_node.neighbors.items():
                    if nb2_id != node_id and nb2_id not in one_hop:
                        nb2_node = network.nodes.get(nb2_id)
                        if nb2_node and nb2_node.battery > 0:
                            two_hop.add(nb2_id)
                            cover_map[nb2_id].add(nb_id)

            if not two_hop:
                # No 2-hop neighbors: this node reaches everything directly
                # Still select some high-quality neighbors as MPRs so the
                # broadcast chain continues beyond this node
                sorted_nbs = sorted(good_neighbors.items(), key=lambda x: -x[1])
                n_mprs = max(2, len(sorted_nbs) // 5)  # top 20% or at least 2
                self._mpr_sets[node_id] = {nb for nb, _ in sorted_nbs[:n_mprs]}
                continue

            mpr = set()
            uncovered = set(two_hop)

            # Step 1: sole providers (only one 1-hop reaches this 2-hop node)
            for th_id in list(uncovered):
                providers = cover_map[th_id]
                if len(providers) == 1:
                    sole = next(iter(providers))
                    mpr.add(sole)
                    sole_node = network.nodes.get(sole)
                    if sole_node:
                        newly_covered = set()
                        for c in sole_node.neighbors:
                            if c in uncovered:
                                newly_covered.add(c)
                        uncovered -= newly_covered

            # Step 2: greedy — pick neighbor with best (coverage * quality) score
            while uncovered:
                best_nb = None
                best_score = -1
                for nb_id in one_hop:
                    if nb_id in mpr:
                        continue
                    nb_node = network.nodes.get(nb_id)
                    if not nb_node or nb_node.battery <= 0:
                        continue
                    covers = sum(1 for c in nb_node.neighbors if c in uncovered)
                    if covers == 0:
                        continue
                    # Score: coverage weighted by link quality to this relay
                    link_q = good_neighbors.get(nb_id, 0.01)
                    score = covers * link_q
                    if score > best_score:
                        best_score = score
                        best_nb = nb_id

                if best_nb is None:
                    break

                mpr.add(best_nb)
                best_node = network.nodes.get(best_nb)
                if best_node:
                    uncovered -= set(best_node.neighbors)

            # Step 3: redundancy — if avg quality of MPR links is low, add extras
            if mpr:
                avg_mpr_q = sum(good_neighbors.get(m, 0.01) for m in mpr) / len(mpr)
                if avg_mpr_q < 0.5:
                    # Add top-quality non-MPR neighbors as backup relays
                    non_mpr = [(nb, q) for nb, q in good_neighbors.items()
                               if nb not in mpr]
                    non_mpr.sort(key=lambda x: -x[1])
                    extras = max(1, len(mpr) // 2)
                    for nb, q in non_mpr[:extras]:
                        mpr.add(nb)

            self._mpr_sets[node_id] = mpr

    # ------------------------------------------------------------------
    # Mode 1: MPR Broadcast (network-wide)
    # ------------------------------------------------------------------

    def broadcast_mpr(self, network, src_id):
        """Broadcast from src_id using MPR relay selection.

        Hybrid approach for lossy LoRa links:
        - Nodes selected as MPR by their sender ALWAYS rebroadcast.
        - Non-MPR nodes probabilistically rebroadcast based on their
          "coverage score": how well they're already covered by MPR
          neighbors that have already rebroadcast.
        - Nodes with few rebroadcasting neighbors (sparse areas) are more
          likely to relay, providing redundancy where it's needed.
        - Nodes with many rebroadcasting neighbors (dense areas) are
          suppressed, saving TX where coverage is already good.

        This combines MPR's relay reduction with robustness for lossy links.
        """
        alive_nodes = sum(1 for n in network.nodes.values() if n.battery > 0)
        stats = BroadcastStats(alive_nodes)

        src_node = network.nodes.get(src_id)
        if not src_node or src_node.battery <= 0:
            return stats

        self._compute_mpr_sets(network)

        seen = {src_id}
        stats.nodes_reached.add(src_id)
        rebroadcasted = {src_id}
        # Queue entries: (node_id, hop_count, is_mpr_for_sender)
        queue = [(src_id, 0, True)]
        sim_time = network.sim_time

        while queue:
            current_id, hop_count, is_mpr = queue.pop(0)
            if hop_count >= self.hop_limit:
                continue

            current_node = network.nodes[current_id]
            if current_node.silent and current_id != src_id:
                continue

            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    continue

            # Decision: should this node rebroadcast?
            should_relay = False
            if current_id == src_id:
                should_relay = True
            elif is_mpr:
                should_relay = True
            else:
                # Non-MPR: suppress if well-covered by rebroadcasting neighbors
                nb_rebroadcasted = sum(
                    1 for nid in current_node.neighbors if nid in rebroadcasted
                )
                # If 2+ neighbors already rebroadcasted, likely redundant
                if nb_rebroadcasted >= 2:
                    # High suppression — most non-MPR nodes in covered areas skip
                    suppress_prob = 0.7 + 0.05 * min(nb_rebroadcasted, 6)
                    should_relay = self.rng.random() >= suppress_prob
                else:
                    # Low coverage: this node is needed for propagation
                    should_relay = True

            if not should_relay:
                continue

            rebroadcasted.add(current_id)
            my_mprs = self._mpr_sets.get(current_id, set())

            for neighbor_id, quality in current_node.neighbors.items():
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue

                neighbor_node = network.nodes.get(neighbor_id)
                if not neighbor_node or neighbor_node.battery <= 0:
                    continue

                seen.add(neighbor_id)
                stats.nodes_reached.add(neighbor_id)

                nb_is_mpr = neighbor_id in my_mprs
                if neighbor_node.neighbors:
                    queue.append((neighbor_id, hop_count + 1, nb_is_mpr))

            if network.enable_half_duplex:
                toa = time_on_air(50, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, sim_time, toa)
                sim_time += toa * 0.3

        stats.energy = stats.total_tx
        return stats

    # ------------------------------------------------------------------
    # Mode 2: Scoped Broadcast (area/group messages)
    # ------------------------------------------------------------------

    def broadcast_scoped(self, network, src_id, max_hops=3):
        """Broadcast limited to max_hops from source.

        Every node within range rebroadcasts (simple flood), but the
        hop counter limits propagation to a local area. Useful for
        group chat, area alerts, etc.
        """
        alive_nodes = sum(1 for n in network.nodes.values() if n.battery > 0)
        stats = BroadcastStats(alive_nodes)

        src_node = network.nodes.get(src_id)
        if not src_node or src_node.battery <= 0:
            return stats

        seen = {src_id}
        stats.nodes_reached.add(src_id)
        queue = [(src_id, 0)]
        sim_time = network.sim_time

        while queue:
            current_id, hop_count = queue.pop(0)
            if hop_count >= max_hops:
                continue

            current_node = network.nodes[current_id]
            if current_node.silent and current_id != src_id:
                continue

            if network.enable_half_duplex:
                if not network.half_duplex.can_transmit(current_id, sim_time):
                    stats.half_duplex_blocked += 1
                    continue

            for neighbor_id, quality in current_node.neighbors.items():
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1

                if self.rng.random() > quality:
                    continue
                if neighbor_id in seen:
                    continue

                neighbor_node = network.nodes.get(neighbor_id)
                if not neighbor_node or neighbor_node.battery <= 0:
                    continue

                seen.add(neighbor_id)
                stats.nodes_reached.add(neighbor_id)

                if neighbor_node.neighbors:
                    queue.append((neighbor_id, hop_count + 1))

            if network.enable_half_duplex:
                toa = time_on_air(50, sf=current_node.sf)
                network.half_duplex.start_tx(current_id, sim_time, toa)
                for nid in current_node.neighbors:
                    network.half_duplex.start_rx(nid, sim_time, toa)
                sim_time += toa * 0.3

        stats.energy = stats.total_tx
        return stats

    # ------------------------------------------------------------------
    # Mode 3: Pull-based (telemetry on demand)
    # ------------------------------------------------------------------

    def pull_telemetry(self, network, requester_id, target_ids, router=None):
        """Request telemetry from specific nodes via unicast.

        Not a broadcast at all — sends individual unicast requests to each
        target node using WalkFloodRouter. Demonstrates that telemetry
        doesn't need broadcast; on-demand pull is far cheaper.

        Args:
            network: MeshNetwork
            requester_id: Node requesting telemetry
            target_ids: List of node IDs to request telemetry from
            router: WalkFloodRouter instance (created if None)

        Returns:
            BroadcastStats (for comparison: nodes_reached = targets that responded)
        """
        alive_nodes = sum(1 for n in network.nodes.values() if n.battery > 0)
        stats = BroadcastStats(alive_nodes)
        stats.nodes_reached.add(requester_id)

        if router is None:
            router = WalkFloodRouter(seed=self.rng.randint(0, 99999))
            router._bootstrapped = False
            router._bootstrap(network)

        for target_id in target_ids:
            pkt = Packet(requester_id, target_id, priority=3, payload_size=20)
            pkt.ttl = 15
            pkt.created_at = network.tick
            result = router.route(network, pkt)
            stats.total_tx += result.total_tx
            for nid, count in result.node_tx_counts.items():
                stats.node_tx_counts[nid] += count
            if result.delivered:
                stats.nodes_reached.add(target_id)

        stats.energy = stats.total_tx
        return stats

    # ------------------------------------------------------------------
    # Convenience: default broadcast (MPR mode)
    # ------------------------------------------------------------------

    def broadcast(self, network, src_id):
        """Default broadcast uses MPR mode (network-wide)."""
        return self.broadcast_mpr(network, src_id)
