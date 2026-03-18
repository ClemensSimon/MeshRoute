"""
Core simulation engine for MeshRoute simulator.
Models nodes, links, packets, routes, clusters, and the mesh network topology.
"""

import random
import math
from collections import defaultdict, deque

from geohash import encode_xy, common_prefix
from lora_model import (
    rssi_from_distance,
    snr_from_rssi,
    link_quality_from_distance,
    packet_success_rate,
    max_range_meters,
    max_range_for_sf,
    time_on_air,
    DutyCycleTracker,
    CollisionModel,
    TERRAIN_PL_EXPONENTS,
)


class Node:
    """A mesh network node with position, battery, queue, and routing state."""

    def __init__(self, node_id, x, y):
        self.id = node_id
        self.x = x
        self.y = y
        self.geohash = ""
        self.cluster_id = None
        self.is_border = False
        self.battery = 100.0
        self.queue = []
        self.neighbors = {}  # node_id -> link_quality (0-1)
        self.routing_table = {}  # dest_id -> list of Route
        self.nhs = 1.0  # network health score (0-1)
        self.mobile = False  # whether this node can move
        self.speed = 0.0  # m/s movement speed
        self.heading = 0.0  # radians
        self.terrain = "urban"  # terrain type at this node's location
        self.sf = 7  # spreading factor (7-12)
        self.airtime_used = 0.0  # total airtime in seconds
        # Stats
        self.packets_sent = 0
        self.packets_forwarded = 0
        self.packets_received = 0
        self.duty_cycle_blocked = 0  # count of duty-cycle rejections

    def distance_to(self, other):
        """Euclidean distance to another node in meters."""
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def load(self):
        """Current load as fraction of queue capacity (max 50 packets)."""
        return min(len(self.queue) / 50.0, 1.0)

    def battery_score(self):
        """Battery level as 0-1 score."""
        return self.battery / 100.0

    def __repr__(self):
        return f"Node({self.id}, ({self.x:.0f},{self.y:.0f}), batt={self.battery:.0f})"


class Link:
    """A radio link between two nodes, potentially asymmetric."""

    def __init__(self, node_a_id, node_b_id, distance, terrain="urban", asymmetry=0.0):
        self.node_a = node_a_id
        self.node_b = node_b_id
        self.distance = distance
        self.terrain = terrain
        self.rssi = rssi_from_distance(distance, terrain=terrain)
        self.snr = snr_from_rssi(self.rssi)
        self.quality = link_quality_from_distance(distance, terrain=terrain)
        # Asymmetric quality: quality_ab != quality_ba
        # asymmetry is a random offset applied differently per direction
        self.quality_ab = max(0.01, min(1.0, self.quality * (1.0 + asymmetry)))
        self.quality_ba = max(0.01, min(1.0, self.quality * (1.0 - asymmetry)))
        self.alive = True

    def quality_from(self, node_id):
        """Get link quality in the direction from node_id."""
        if node_id == self.node_a:
            return self.quality_ab
        return self.quality_ba

    def other(self, node_id):
        """Return the other end of the link."""
        return self.node_b if node_id == self.node_a else self.node_a

    def __repr__(self):
        return f"Link({self.node_a}<->{self.node_b}, q={self.quality:.2f})"


class Packet:
    """A message packet traversing the mesh."""

    _next_id = 0

    def __init__(self, src, dst, priority=3, payload_size=50):
        Packet._next_id += 1
        self.id = Packet._next_id
        self.src = src
        self.dst = dst
        self.priority = priority  # 0-7 QoS class (0=highest)
        self.payload_size = payload_size  # bytes
        self.hops = []  # list of node IDs traversed
        self.created_at = 0  # simulation tick
        self.delivered_at = None
        self.ttl = 30  # max hops

    def is_delivered(self):
        return self.delivered_at is not None

    def latency(self):
        """Latency in hops."""
        return len(self.hops) if self.is_delivered() else -1

    def __repr__(self):
        status = f"delivered@{self.delivered_at}" if self.is_delivered() else "in-flight"
        return f"Packet({self.id}, {self.src}->{self.dst}, {status})"


class Route:
    """A cached route through the mesh."""

    def __init__(self, path, quality=1.0, load=0.0, battery=1.0):
        self.path = list(path)  # list of node IDs
        self.quality = quality
        self.load = load
        self.battery = battery
        self.weight = 0.0
        self.compute_weight()

    def compute_weight(self, alpha=0.4, beta=0.35, gamma=0.25):
        """W(r) = alpha*Q * beta*(1-Load) * gamma*Batt"""
        self.weight = (
            alpha * self.quality
            + beta * (1.0 - self.load)
            + gamma * self.battery
        )

    def hop_count(self):
        return len(self.path) - 1

    def __repr__(self):
        return f"Route({' -> '.join(str(n) for n in self.path)}, w={self.weight:.3f})"


class Cluster:
    """A geographic cluster of nodes sharing a geohash prefix."""

    def __init__(self, cluster_id, geohash_prefix):
        self.id = cluster_id
        self.geohash_prefix = geohash_prefix
        self.members = []  # list of node IDs
        self.border_nodes = []  # list of node IDs

    def __repr__(self):
        return f"Cluster({self.id}, '{self.geohash_prefix}', {len(self.members)} nodes)"


class MeshNetwork:
    """The complete mesh network simulation."""

    def __init__(self, seed=42):
        self.rng = random.Random(seed)
        self.nodes = {}  # id -> Node
        self.links = []  # list of Link
        self.link_map = {}  # (a,b) -> Link (sorted tuple keys)
        self.clusters = {}  # id -> Cluster
        self.tick = 0
        self.sim_time = 0.0  # simulation time in seconds
        self.terrain = "urban"  # default terrain
        self.asymmetry = 0.0  # link asymmetry factor (0=symmetric, 0.3=moderate)
        self.duty_cycle = DutyCycleTracker()
        self.collisions = CollisionModel()
        self.enable_duty_cycle = False
        self.enable_collisions = False
        self.mobile_fraction = 0.0  # fraction of mobile nodes

    def build_topology(self, n_nodes, area_size, lora_range=2000,
                       terrain="urban", asymmetry=0.0, mobile_fraction=0.0,
                       placement="random"):
        """Build a mesh topology.

        Args:
            n_nodes: Number of nodes to place
            area_size: Side length of square area in meters
            lora_range: Maximum LoRa communication range in meters
            terrain: Default terrain type
            asymmetry: Link asymmetry factor (0=symmetric, 0.3=moderate)
            mobile_fraction: Fraction of nodes that are mobile (0-1)
            placement: Node placement strategy (random, grid, linear, clustered)
        """
        self.area_size = area_size
        self.lora_range = lora_range
        self.terrain = terrain
        self.asymmetry = asymmetry
        self.mobile_fraction = mobile_fraction

        # Place nodes based on strategy
        if placement == "linear":
            self._place_linear(n_nodes, area_size)
        elif placement == "clustered":
            self._place_clustered(n_nodes, area_size)
        else:
            self._place_random(n_nodes, area_size)

        # Set terrain and mobility
        for node in self.nodes.values():
            node.terrain = terrain

        # Mark mobile nodes
        if mobile_fraction > 0:
            n_mobile = max(1, int(n_nodes * mobile_fraction))
            mobile_ids = self.rng.sample(list(self.nodes.keys()), n_mobile)
            for nid in mobile_ids:
                node = self.nodes[nid]
                node.mobile = True
                node.speed = self.rng.uniform(0.5, 2.0)  # 0.5-2.0 m/s (walking)
                node.heading = self.rng.uniform(0, 2 * math.pi)

        # Create links between nodes within range
        self._create_links()

        # Ensure connectivity: connect isolated nodes to nearest
        self._ensure_connectivity()

    def _place_random(self, n_nodes, area_size):
        """Place nodes randomly in the area."""
        for i in range(n_nodes):
            x = self.rng.uniform(0, area_size)
            y = self.rng.uniform(0, area_size)
            node = Node(i, x, y)
            node.battery = self.rng.uniform(50, 100)
            self.nodes[i] = node

    def _place_linear(self, n_nodes, area_size):
        """Place nodes along a line (trail/road scenario) with some scatter."""
        for i in range(n_nodes):
            # Main line from (0,h/2) to (w,h/2) with perpendicular scatter
            t = i / max(n_nodes - 1, 1)
            x = t * area_size
            y = area_size / 2 + self.rng.gauss(0, area_size * 0.05)
            y = max(0, min(area_size, y))
            node = Node(i, x, y)
            node.battery = self.rng.uniform(50, 100)
            self.nodes[i] = node

    def _place_clustered(self, n_nodes, area_size):
        """Place nodes in natural clusters (villages/buildings)."""
        n_clusters = max(3, n_nodes // 15)
        centers = [(self.rng.uniform(area_size * 0.1, area_size * 0.9),
                     self.rng.uniform(area_size * 0.1, area_size * 0.9))
                    for _ in range(n_clusters)]

        for i in range(n_nodes):
            cx, cy = self.rng.choice(centers)
            spread = area_size * 0.08
            x = max(0, min(area_size, cx + self.rng.gauss(0, spread)))
            y = max(0, min(area_size, cy + self.rng.gauss(0, spread)))
            node = Node(i, x, y)
            node.battery = self.rng.uniform(50, 100)
            self.nodes[i] = node

    def _create_links(self):
        """Create links between nodes within range."""
        node_list = list(self.nodes.values())
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                na = node_list[i]
                nb = node_list[j]
                dist = na.distance_to(nb)
                if dist <= self.lora_range:
                    asym = self.rng.uniform(-self.asymmetry, self.asymmetry) if self.asymmetry > 0 else 0.0
                    link = Link(na.id, nb.id, dist, terrain=self.terrain, asymmetry=asym)
                    if link.quality > 0.01:
                        self.links.append(link)
                        key = (min(na.id, nb.id), max(na.id, nb.id))
                        self.link_map[key] = link
                        na.neighbors[nb.id] = link.quality_ab
                        nb.neighbors[na.id] = link.quality_ba

    def _ensure_connectivity(self):
        """Connect isolated nodes to their nearest neighbor."""
        if not self.nodes:
            return

        # Find connected components using BFS
        visited = set()
        components = []
        for start in self.nodes:
            if start in visited:
                continue
            component = set()
            queue = deque([start])
            while queue:
                node_id = queue.popleft()
                if node_id in component:
                    continue
                component.add(node_id)
                visited.add(node_id)
                for neighbor_id in self.nodes[node_id].neighbors:
                    if neighbor_id not in component:
                        queue.append(neighbor_id)
            components.append(component)

        if len(components) <= 1:
            return

        # Connect each component to the largest one
        largest = max(components, key=len)
        for comp in components:
            if comp is largest:
                continue
            # Find closest pair between comp and largest
            best_dist = float("inf")
            best_pair = None
            for a_id in comp:
                for b_id in largest:
                    dist = self.nodes[a_id].distance_to(self.nodes[b_id])
                    if dist < best_dist:
                        best_dist = dist
                        best_pair = (a_id, b_id)

            if best_pair:
                a_id, b_id = best_pair
                link = Link(a_id, b_id, best_dist)
                # Force minimum quality for connectivity links
                link.quality = max(link.quality, 0.1)
                self.links.append(link)
                key = (min(a_id, b_id), max(a_id, b_id))
                self.link_map[key] = link
                self.nodes[a_id].neighbors[b_id] = link.quality
                self.nodes[b_id].neighbors[a_id] = link.quality

    def move_mobile_nodes(self, dt=1.0):
        """Move mobile nodes by one time step.

        Nodes bounce off area boundaries. After movement, links are
        recalculated for all mobile nodes.

        Args:
            dt: Time step in seconds
        """
        moved = []
        for node in self.nodes.values():
            if not node.mobile or node.battery <= 0:
                continue

            # Random walk with momentum
            node.heading += self.rng.gauss(0, 0.3)  # slight direction change
            dx = math.cos(node.heading) * node.speed * dt
            dy = math.sin(node.heading) * node.speed * dt

            new_x = node.x + dx
            new_y = node.y + dy

            # Bounce off boundaries
            if new_x < 0 or new_x > self.area_size:
                node.heading = math.pi - node.heading
                new_x = max(0, min(self.area_size, new_x))
            if new_y < 0 or new_y > self.area_size:
                node.heading = -node.heading
                new_y = max(0, min(self.area_size, new_y))

            node.x = new_x
            node.y = new_y
            moved.append(node.id)

        if moved:
            self._refresh_links_for(moved)

    def _refresh_links_for(self, node_ids):
        """Recalculate links for specific nodes after movement."""
        node_set = set(node_ids)

        # Remove old links involving moved nodes
        old_links = [l for l in self.links
                     if l.node_a in node_set or l.node_b in node_set]
        for link in old_links:
            self.links.remove(link)
            key = (min(link.node_a, link.node_b), max(link.node_a, link.node_b))
            self.link_map.pop(key, None)
            na = self.nodes[link.node_a]
            nb = self.nodes[link.node_b]
            na.neighbors.pop(link.node_b, None)
            nb.neighbors.pop(link.node_a, None)

        # Create new links
        for nid in node_ids:
            node = self.nodes[nid]
            if node.battery <= 0:
                continue
            for other in self.nodes.values():
                if other.id == nid or other.battery <= 0:
                    continue
                key = (min(nid, other.id), max(nid, other.id))
                if key in self.link_map:
                    continue
                dist = node.distance_to(other)
                if dist <= self.lora_range:
                    asym = self.rng.uniform(-self.asymmetry, self.asymmetry) if self.asymmetry > 0 else 0.0
                    link = Link(nid, other.id, dist, terrain=self.terrain, asymmetry=asym)
                    if link.quality > 0.01:
                        self.links.append(link)
                        self.link_map[key] = link
                        node.neighbors[other.id] = link.quality_ab if nid == link.node_a else link.quality_ba
                        other.neighbors[nid] = link.quality_ba if nid == link.node_a else link.quality_ab

    def compute_geohash_clusters(self, prefix_length=4):
        """Assign nodes to clusters based on geohash prefix.

        Args:
            prefix_length: Number of geohash characters for clustering
        """
        # Compute geohashes
        for node in self.nodes.values():
            node.geohash = encode_xy(node.x, node.y, self.area_size, precision=6)

        # Group by prefix
        prefix_groups = defaultdict(list)
        for node in self.nodes.values():
            prefix = node.geohash[:prefix_length]
            prefix_groups[prefix].append(node.id)

        # If everything falls in one cluster (small area), try shorter prefix
        # or accept single cluster
        self.clusters = {}
        for idx, (prefix, members) in enumerate(sorted(prefix_groups.items())):
            cluster = Cluster(idx, prefix)
            cluster.members = members
            self.clusters[idx] = cluster
            for nid in members:
                self.nodes[nid].cluster_id = idx

        # If only 1 cluster and many nodes, subdivide artificially by quadrant
        if len(self.clusters) == 1 and len(self.nodes) > 10:
            self._subdivide_by_quadrant()

    def _subdivide_by_quadrant(self):
        """Subdivide a single cluster into quadrants for better routing."""
        self.clusters = {}
        half = self.area_size / 2.0
        for node in self.nodes.values():
            qx = 0 if node.x < half else 1
            qy = 0 if node.y < half else 2
            cid = qx + qy
            node.cluster_id = cid
            if cid not in self.clusters:
                self.clusters[cid] = Cluster(cid, f"Q{cid}")
            self.clusters[cid].members.append(node.id)

    def elect_border_nodes(self):
        """Mark nodes that have neighbors in other clusters as border nodes."""
        for node in self.nodes.values():
            node.is_border = False

        for node in self.nodes.values():
            for neighbor_id in node.neighbors:
                neighbor = self.nodes[neighbor_id]
                if neighbor.cluster_id != node.cluster_id:
                    node.is_border = True
                    break

        # Update cluster border node lists
        for cluster in self.clusters.values():
            cluster.border_nodes = [
                nid for nid in cluster.members if self.nodes[nid].is_border
            ]

    def run_ogm_round(self):
        """Simulate a B.A.T.M.A.N. OGM (Originator Message) round.

        Each node broadcasts an OGM, and neighbors update link quality
        based on reception probability. Asymmetric — each direction gets
        independent noise.
        """
        for link in self.links:
            if not link.alive:
                continue
            # Independent noise per direction (asymmetric fading)
            noise_ab = self.rng.gauss(0, 0.05)
            noise_ba = self.rng.gauss(0, 0.05)
            link.quality_ab = max(0.01, min(1.0, link.quality_ab + noise_ab))
            link.quality_ba = max(0.01, min(1.0, link.quality_ba + noise_ba))
            link.quality = (link.quality_ab + link.quality_ba) / 2.0

            na = self.nodes[link.node_a]
            nb = self.nodes[link.node_b]
            if link.node_b in na.neighbors:
                na.neighbors[link.node_b] = link.quality_ab
            if link.node_a in nb.neighbors:
                nb.neighbors[link.node_a] = link.quality_ba

    def compute_routes(self, max_routes=5, max_hops=15):
        """Compute multi-path routes using BFS for all node pairs.

        For large networks (>200 nodes), uses lazy route computation
        to avoid O(N^2) upfront cost. Routes are computed on first access.

        Args:
            max_routes: Maximum routes to store per destination
            max_hops: Maximum hops per route
        """
        self._max_routes = max_routes
        self._max_hops = max_hops

        # For small networks, precompute all routes
        if len(self.nodes) <= 200:
            for src_node in self.nodes.values():
                src_node.routing_table = {}
                for dst_id in self.nodes:
                    if dst_id == src_node.id:
                        continue
                    routes = self._find_routes_bfs(src_node.id, dst_id, max_routes, max_hops)
                    if routes:
                        src_node.routing_table[dst_id] = routes
        else:
            # Large network: clear tables, compute lazily via get_routes()
            for src_node in self.nodes.values():
                src_node.routing_table = {}

    def get_routes(self, src_id, dst_id):
        """Get routes from src to dst, computing lazily if needed."""
        src_node = self.nodes[src_id]
        if dst_id not in src_node.routing_table:
            routes = self._find_routes_bfs(
                src_id, dst_id, self._max_routes, self._max_hops
            )
            if routes:
                src_node.routing_table[dst_id] = routes
            else:
                src_node.routing_table[dst_id] = []
        return src_node.routing_table.get(dst_id, [])

    def _find_routes_bfs(self, src_id, dst_id, max_routes, max_hops):
        """Find multiple routes using modified BFS (Yen's k-shortest paths simplified).

        Uses iterative BFS with path exclusion to find diverse routes.
        """
        routes = []

        # First: find shortest path via BFS
        first_path = self._bfs_path(src_id, dst_id, max_hops, set())
        if not first_path:
            return routes

        route = self._path_to_route(first_path)
        routes.append(route)

        # Find alternative paths by excluding intermediate nodes of previous paths
        used_intermediates = set()
        for path in [first_path]:
            for nid in path[1:-1]:  # exclude src and dst
                used_intermediates.add(nid)

        for _ in range(max_routes - 1):
            # Try excluding subsets of used nodes to find diverse paths
            alt_path = self._bfs_path(src_id, dst_id, max_hops, used_intermediates)
            if alt_path and alt_path not in [r.path for r in routes]:
                route = self._path_to_route(alt_path)
                routes.append(route)
                for nid in alt_path[1:-1]:
                    used_intermediates.add(nid)
            else:
                # Try excluding just one node at a time from the best path
                for exclude_node in first_path[1:-1]:
                    if len(routes) >= max_routes:
                        break
                    alt = self._bfs_path(src_id, dst_id, max_hops, {exclude_node})
                    if alt and alt not in [r.path for r in routes]:
                        route = self._path_to_route(alt)
                        routes.append(route)
                break

        return routes[:max_routes]

    def _bfs_path(self, src_id, dst_id, max_hops, excluded):
        """BFS shortest path avoiding excluded nodes."""
        if src_id == dst_id:
            return [src_id]

        visited = {src_id}
        queue = deque([(src_id, [src_id])])

        while queue:
            current, path = queue.popleft()
            if len(path) > max_hops:
                continue

            # Sort neighbors by link quality (best first) for better paths
            neighbors = sorted(
                self.nodes[current].neighbors.items(),
                key=lambda x: x[1],
                reverse=True,
            )

            for neighbor_id, quality in neighbors:
                if neighbor_id in visited:
                    continue
                if neighbor_id in excluded:
                    continue

                # Check link is alive
                key = (min(current, neighbor_id), max(current, neighbor_id))
                link = self.link_map.get(key)
                if link and not link.alive:
                    continue

                new_path = path + [neighbor_id]
                if neighbor_id == dst_id:
                    return new_path

                visited.add(neighbor_id)
                queue.append((neighbor_id, new_path))

        return None

    def _path_to_route(self, path):
        """Convert a node ID path to a Route with quality metrics."""
        if len(path) < 2:
            return Route(path)

        # Quality = product of link qualities along the path
        quality = 1.0
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            key = (min(a, b), max(a, b))
            link = self.link_map.get(key)
            if link:
                quality *= link.quality
            else:
                quality *= 0.1  # connectivity link

        # Load = average load of intermediate nodes
        if len(path) > 2:
            loads = [self.nodes[nid].load() for nid in path[1:-1]]
            avg_load = sum(loads) / len(loads)
        else:
            avg_load = 0.0

        # Battery = minimum battery along path (weakest link)
        batteries = [self.nodes[nid].battery_score() for nid in path[1:]]
        min_battery = min(batteries) if batteries else 1.0

        route = Route(path, quality=quality, load=avg_load, battery=min_battery)
        return route

    def compute_nhs(self):
        """Compute Network Health Score per cluster.

        NHS is based on: average link quality, average battery, and connectivity.
        Range: 0.0 (critical) to 1.0 (healthy).
        """
        for cluster in self.clusters.values():
            if not cluster.members:
                continue

            # Average link quality within cluster
            qualities = []
            for nid in cluster.members:
                node = self.nodes[nid]
                for neighbor_id, q in node.neighbors.items():
                    if self.nodes[neighbor_id].cluster_id == cluster.id:
                        qualities.append(q)

            avg_quality = sum(qualities) / len(qualities) if qualities else 0.5

            # Average battery
            batteries = [self.nodes[nid].battery_score() for nid in cluster.members]
            avg_battery = sum(batteries) / len(batteries) if batteries else 0.5

            # Connectivity score: fraction of nodes with >= 2 neighbors
            connected = sum(
                1 for nid in cluster.members if len(self.nodes[nid].neighbors) >= 2
            )
            connectivity = connected / len(cluster.members)

            nhs = 0.4 * avg_quality + 0.3 * avg_battery + 0.3 * connectivity
            nhs = max(0.0, min(1.0, nhs))

            for nid in cluster.members:
                self.nodes[nid].nhs = nhs

    def get_link(self, a_id, b_id):
        """Get the link between two nodes."""
        key = (min(a_id, b_id), max(a_id, b_id))
        return self.link_map.get(key)

    def kill_node(self, node_id):
        """Simulate node failure: disable all its links."""
        node = self.nodes[node_id]
        node.battery = 0
        for neighbor_id in list(node.neighbors.keys()):
            key = (min(node_id, neighbor_id), max(node_id, neighbor_id))
            link = self.link_map.get(key)
            if link:
                link.alive = False
            # Remove from neighbor's neighbor list
            if node_id in self.nodes[neighbor_id].neighbors:
                del self.nodes[neighbor_id].neighbors[node_id]
        node.neighbors.clear()

    def degrade_links(self, loss_fraction):
        """Randomly degrade a fraction of links (simulate poor conditions).

        Args:
            loss_fraction: Fraction of links to degrade (0-1)
        """
        n_degrade = int(len(self.links) * loss_fraction)
        targets = self.rng.sample(self.links, min(n_degrade, len(self.links)))
        for link in targets:
            factor = self.rng.uniform(0.1, 0.5)
            link.quality *= factor
            link.quality_ab *= factor
            link.quality_ba *= factor
            self.nodes[link.node_a].neighbors[link.node_b] = link.quality_ab
            self.nodes[link.node_b].neighbors[link.node_a] = link.quality_ba

    def stats_summary(self):
        """Return a summary dict of network statistics."""
        n_nodes = len(self.nodes)
        n_links = sum(1 for l in self.links if l.alive)
        n_clusters = len(self.clusters)
        avg_neighbors = (
            sum(len(n.neighbors) for n in self.nodes.values()) / n_nodes
            if n_nodes
            else 0
        )
        avg_routes = 0
        route_counts = []
        if n_nodes <= 200:
            # Precomputed: scan all tables
            for node in self.nodes.values():
                for routes in node.routing_table.values():
                    route_counts.append(len(routes))
        else:
            # Lazy: sample ~50 random pairs to estimate
            node_ids = list(self.nodes.keys())
            sample_size = min(50, n_nodes)
            for _ in range(sample_size):
                src = self.rng.choice(node_ids)
                dst = self.rng.choice(node_ids)
                if src != dst:
                    routes = self.get_routes(src, dst)
                    route_counts.append(len(routes))
        if route_counts:
            avg_routes = sum(route_counts) / len(route_counts)

        return {
            "nodes": n_nodes,
            "links": n_links,
            "clusters": n_clusters,
            "avg_neighbors": round(avg_neighbors, 1),
            "avg_routes_per_dest": round(avg_routes, 1),
        }

    def ascii_visualization(self, width=60, height=30):
        """Generate an ASCII art visualization of the network.

        Args:
            width: Terminal width in characters
            height: Terminal height in characters

        Returns:
            Multi-line string with the visualization
        """
        if not self.nodes:
            return "  (empty network)"

        grid = [[" " for _ in range(width)] for _ in range(height)]

        # Map node positions to grid
        node_positions = {}
        for node in self.nodes.values():
            gx = int(node.x / self.area_size * (width - 1))
            gy = int(node.y / self.area_size * (height - 1))
            gx = max(0, min(width - 1, gx))
            gy = max(0, min(height - 1, gy))
            # Flip y so 0 is at bottom
            gy = height - 1 - gy
            node_positions[node.id] = (gx, gy)

        # Draw links first (as dots)
        for link in self.links:
            if not link.alive:
                continue
            ax, ay = node_positions.get(link.node_a, (0, 0))
            bx, by = node_positions.get(link.node_b, (0, 0))
            # Simple line drawing: just mark midpoint
            mx, my = (ax + bx) // 2, (ay + by) // 2
            if grid[my][mx] == " ":
                grid[my][mx] = "."

        # Draw nodes (overwrite links)
        cluster_chars = "0123456789ABCDEF"
        for node in self.nodes.values():
            gx, gy = node_positions[node.id]
            cid = node.cluster_id if node.cluster_id is not None else 0
            char = cluster_chars[cid % len(cluster_chars)]
            if node.is_border:
                char = "*"
            if node.battery <= 0:
                char = "X"
            grid[gy][gx] = char

        # Build output
        border_h = "+" + "-" * width + "+"
        lines = [border_h]
        for row in grid:
            lines.append("|" + "".join(row) + "|")
        lines.append(border_h)

        # Legend
        lines.append("  0-F = cluster ID, * = border node, X = dead node, . = link")

        return "\n".join(lines)
