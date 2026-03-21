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

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.qos_stats = defaultdict(lambda: {"sent": 0, "delivered": 0})
        self.fallback_used = 0
        self.route_switches = 0

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

        routes = network.get_routes(src_node.id, dst_id)

        if routes:
            valid_routes = []
            for route in routes:
                alive = all(
                    network.nodes[nid].battery > 0 for nid in route.path
                )
                if alive:
                    valid_routes.append(route)

            tried = set()
            for attempt in range(min(len(valid_routes), 5)):  # try up to 5 routes
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
                    self.qos_stats[packet.priority]["delivered"] += 1
                    stats.energy = stats.total_tx
                    return stats

        self.fallback_used += 1
        if self._fallback_cluster_flood(network, packet, stats):
            self.qos_stats[packet.priority]["delivered"] += 1

        stats.energy = stats.total_tx
        return stats
