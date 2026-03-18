"""
Routing algorithms for MeshRoute simulator.
Implements Flooding and System 5 geo-clustered multi-path routing.
"""

import random
from collections import defaultdict

from lora_model import packet_success_rate, time_on_air


class RoutingStats:
    """Statistics from routing a single packet."""

    def __init__(self):
        self.delivered = False
        self.total_tx = 0  # total transmissions across all nodes
        self.hops = 0  # hops to destination (0 if not delivered)
        self.path = []  # actual path taken
        self.energy = 0.0  # energy consumed (proportional to tx count)
        self.node_tx_counts = defaultdict(int)  # per-node transmission count

    def __repr__(self):
        status = "OK" if self.delivered else "FAIL"
        return f"Stats({status}, tx={self.total_tx}, hops={self.hops})"


class FloodingRouter:
    """Naive flooding: every node rebroadcasts every packet once.

    Simple but bandwidth-expensive. Used as the baseline comparison.
    """

    def __init__(self, seed=42):
        self.rng = random.Random(seed)

    def route(self, network, packet):
        """Route a packet using flooding.

        Args:
            network: MeshNetwork instance
            packet: Packet to route

        Returns:
            RoutingStats with results
        """
        stats = RoutingStats()

        # Check src and dst exist and are alive
        if packet.src not in network.nodes or packet.dst not in network.nodes:
            return stats
        if network.nodes[packet.src].battery <= 0:
            return stats
        if not network.nodes[packet.src].neighbors:
            return stats

        seen = {packet.src}  # nodes that have seen this packet
        # Queue: (node_id, hop_count, path)
        broadcast_queue = [(packet.src, 0, [packet.src])]
        delivery_path = None

        while broadcast_queue:
            current_id, hop_count, path = broadcast_queue.pop(0)

            if hop_count >= packet.ttl:
                continue

            current_node = network.nodes[current_id]

            # Broadcast to all neighbors
            for neighbor_id, quality in current_node.neighbors.items():
                # Simulate transmission
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                current_node.packets_forwarded += 1

                # Check if transmission succeeds (probabilistic)
                if self.rng.random() > quality:
                    continue  # packet lost on this link

                if neighbor_id in seen:
                    continue  # duplicate suppression

                seen.add(neighbor_id)
                new_path = path + [neighbor_id]

                if neighbor_id == packet.dst:
                    # Delivered!
                    if delivery_path is None or len(new_path) < len(delivery_path):
                        delivery_path = new_path
                    # In flooding, we continue broadcasting even after delivery
                    # (nodes don't know it was delivered)
                    continue

                neighbor_node = network.nodes[neighbor_id]
                if neighbor_node.battery <= 0:
                    continue
                if not neighbor_node.neighbors:
                    continue

                broadcast_queue.append((neighbor_id, hop_count + 1, new_path))

        if delivery_path:
            stats.delivered = True
            stats.hops = len(delivery_path) - 1
            stats.path = delivery_path
            packet.hops = delivery_path
            packet.delivered_at = network.tick
            network.nodes[packet.dst].packets_received += 1

        stats.energy = stats.total_tx  # simple energy model: 1 unit per TX
        return stats


class System5Router:
    """System 5: Geo-clustered multi-path load-balanced routing.

    Features:
    - Uses pre-computed multi-path routes
    - Weighted route selection: W(r) = alpha*Q + beta*(1-Load) + gamma*Batt
    - Proportional load distribution across routes
    - QoS gate based on local Network Health Score
    - Back-pressure: avoids overloaded nodes
    """

    # Weight parameters
    ALPHA = 0.4   # quality weight
    BETA = 0.35   # load weight
    GAMMA = 0.25  # battery weight

    # QoS gate thresholds: maps NHS level to max allowed priority
    # NHS >= threshold -> allow packets up to priority level
    NHS_THRESHOLDS = {
        0.8: 7,  # healthy: allow all priorities
        0.6: 5,  # moderate: drop lowest 2 priorities
        0.4: 3,  # degraded: only medium and above
        0.2: 1,  # critical: only highest priorities
        0.0: 0,  # emergency: only priority 0
    }

    # Back-pressure: skip nodes with queue load above this
    BACKPRESSURE_THRESHOLD = 0.8

    def __init__(self, seed=42):
        self.rng = random.Random(seed)

    def _qos_gate(self, node, packet):
        """Check if packet passes the QoS gate based on local NHS.

        Returns True if the packet is allowed through.
        """
        nhs = node.nhs
        max_priority = 0
        for threshold, priority in sorted(self.NHS_THRESHOLDS.items(), reverse=True):
            if nhs >= threshold:
                max_priority = priority
                break
        return packet.priority <= max_priority

    def _select_route(self, routes, network):
        """Select a route using weighted proportional selection.

        Recomputes weights based on current network state, then
        selects proportionally to weight.

        Args:
            routes: List of Route objects
            network: MeshNetwork for current state

        Returns:
            Selected Route, or None if all routes are dead
        """
        # Recompute weights with current state
        valid_routes = []
        for route in routes:
            # Check all links in route are alive and nodes are alive
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

            # Recompute quality, load, battery
            quality = 1.0
            for i in range(len(route.path) - 1):
                link = network.get_link(route.path[i], route.path[i + 1])
                if link:
                    quality *= link.quality

            intermediates = route.path[1:-1]
            if intermediates:
                loads = [network.nodes[nid].load() for nid in intermediates]
                avg_load = sum(loads) / len(loads)
                # Back-pressure check
                if max(loads) > self.BACKPRESSURE_THRESHOLD:
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

        # Proportional selection
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

    def route(self, network, packet):
        """Route a packet using System 5 algorithm.

        Args:
            network: MeshNetwork instance
            packet: Packet to route

        Returns:
            RoutingStats with results
        """
        stats = RoutingStats()

        src_node = network.nodes.get(packet.src)
        if not src_node or src_node.battery <= 0:
            return stats

        dst_id = packet.dst
        if dst_id not in network.nodes:
            return stats

        # QoS gate at source
        if not self._qos_gate(src_node, packet):
            return stats  # packet dropped by QoS

        # Look up routes (use get_routes for lazy computation in large networks)
        routes = network.get_routes(src_node.id, dst_id)
        if not routes:
            return stats  # no route known

        # Select best route
        selected = self._select_route(routes, network)
        if not selected:
            return stats  # all routes dead

        # Forward packet along selected route
        path = selected.path
        current_path = [path[0]]

        for i in range(len(path) - 1):
            current_id = path[i]
            next_id = path[i + 1]

            current_node = network.nodes[current_id]
            link = network.get_link(current_id, next_id)

            # Transmit
            stats.total_tx += 1
            stats.node_tx_counts[current_id] += 1
            current_node.packets_forwarded += 1

            # Simulate battery drain
            current_node.battery = max(0, current_node.battery - 0.01)

            # Check link success
            quality = link.quality if link else 0.1
            if self.rng.random() > quality:
                # Transmission failed — try retransmit once
                stats.total_tx += 1
                stats.node_tx_counts[current_id] += 1
                if self.rng.random() > quality:
                    # Second attempt also failed — packet lost
                    break

            next_node = network.nodes[next_id]
            if next_node.battery <= 0:
                break

            current_path.append(next_id)

            # Add to queue (simulate processing)
            next_node.queue.append(packet.id)
            if len(next_node.queue) > 50:
                next_node.queue.pop(0)  # drop oldest

            if next_id == dst_id:
                # Delivered!
                stats.delivered = True
                stats.hops = len(current_path) - 1
                stats.path = current_path
                packet.hops = current_path
                packet.delivered_at = network.tick
                next_node.packets_received += 1
                break

            # QoS gate at intermediate node
            if not self._qos_gate(next_node, packet):
                break  # dropped by QoS at intermediate

        # Clean up queues
        for nid in current_path:
            node = network.nodes[nid]
            if packet.id in node.queue:
                node.queue.remove(packet.id)

        stats.energy = stats.total_tx
        return stats
