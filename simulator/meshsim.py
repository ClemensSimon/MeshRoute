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
    HalfDuplexRadio,
    TERRAIN_PL_EXPONENTS,
)


class Node:
    """A mesh network node with position, battery, queue, and routing state."""

    def __init__(self, node_id, x, y):
        self.id = node_id
        self.x = x
        self.y = y
        self.elevation = 0.0  # meters above sea level
        self.tx_range = 0  # effective TX range in meters (0 = use network default)
        self.node_tier = "valley"  # mountain, hill, valley — for topology reports
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
        # Silencing
        self.silent = False  # if True: node listens but does NOT rebroadcast/send OGMs
        self.silence_until = 0.0  # sim_time when silence expires (rotation)
        self.redundancy_score = 0.0  # 0=critical (only path), 1=fully redundant
        self.silence_priority = 0.0  # higher = more likely to be silenced
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
        self.half_duplex = HalfDuplexRadio()
        self.enable_duty_cycle = False
        self.enable_collisions = False
        self.enable_half_duplex = False
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
        elif placement == "bay_area":
            self._place_bay_area(n_nodes, area_size)
        else:
            self._place_random(n_nodes, area_size)

        # Set terrain and mobility
        # (bay_area placement sets per-node terrain, don't override)
        if placement != "bay_area":
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

    def _place_bay_area(self, n_nodes, area_size):
        """Place nodes in a Bay Area-style 3-tier elevation topology.

        Models the real Bay Area Mesh structure:
        - Mountain nodes (2000+ ft / 600+ m): 5 nodes on ridgelines, 30+ mile range,
          can hear everything but are blocked by half-duplex when lower tiers TX
        - Hill/Rooftop nodes (200-600m): ~15% of nodes, 5-10 mile range,
          bridge between mountains and valleys
        - Valley/Indoor nodes (0-200m): ~80% of nodes, 1-2 mile range,
          typical handheld/indoor clients

        Each tier has different terrain characteristics and TX ranges.
        """
        node_id = 0

        # Tier distribution (matches Bay Area Mesh description)
        n_mountain = max(3, int(n_nodes * 0.03))  # ~3% mountain routers
        n_hill = max(5, int(n_nodes * 0.15))       # ~15% hill/rooftop
        n_valley = n_nodes - n_mountain - n_hill    # ~82% valley/indoor

        # Mountain ridgeline positions (spread along area edges at high points)
        # Bay Area: Mt Diablo, Mt Tam, Mt Hamilton, Sunol Ridge, San Bruno Mtn
        for i in range(n_mountain):
            angle = (i / n_mountain) * 2 * math.pi
            radius = area_size * 0.30
            cx = area_size / 2 + radius * math.cos(angle)
            cy = area_size / 2 + radius * math.sin(angle)
            x = max(0, min(area_size, cx + self.rng.gauss(0, area_size * 0.03)))
            y = max(0, min(area_size, cy + self.rng.gauss(0, area_size * 0.03)))

            node = Node(node_id, x, y)
            node.elevation = self.rng.uniform(600, 1200)  # 600-1200m (2000-4000 ft)
            node.node_tier = "mountain"
            node.terrain = "free_space"  # clear line-of-sight from peaks
            node.tx_range = int(area_size * 0.9)  # can reach across entire area
            node.battery = 100.0  # solar-powered, always full
            node.sf = 12  # long range spreading factor
            self.nodes[node_id] = node
            node_id += 1

        # Hill/Rooftop nodes — scattered around populated areas, key bridges
        # These are the critical relays between mountains and valleys
        hill_centers = [
            (area_size * 0.3, area_size * 0.3),   # Oakland hills
            (area_size * 0.7, area_size * 0.4),   # SF hills (Twin Peaks etc)
            (area_size * 0.5, area_size * 0.7),   # Peninsula hills
            (area_size * 0.2, area_size * 0.6),   # Marin headlands
            (area_size * 0.6, area_size * 0.65),  # San Bruno Mountain
        ]
        for i in range(n_hill):
            cx, cy = self.rng.choice(hill_centers)
            x = max(0, min(area_size, cx + self.rng.gauss(0, area_size * 0.06)))
            y = max(0, min(area_size, cy + self.rng.gauss(0, area_size * 0.06)))

            node = Node(node_id, x, y)
            node.elevation = self.rng.uniform(150, 500)  # 150-500m (500-1600 ft)
            node.node_tier = "hill"
            node.terrain = "suburban"  # partial obstructions
            node.tx_range = int(area_size * 0.20)  # ~10 miles at 50km area
            node.battery = self.rng.uniform(70, 100)  # rooftop = usually powered
            node.sf = 10  # moderate range SF
            self.nodes[node_id] = node
            node_id += 1

        # Valley/Indoor nodes — dense clusters near hill centers (realistic:
        # people live near the hills, not randomly across the Bay)
        valley_centers = [
            (area_size * 0.35, area_size * 0.35),  # downtown Oakland
            (area_size * 0.65, area_size * 0.45),  # SF downtown
            (area_size * 0.5, area_size * 0.65),   # San Mateo / Redwood City
            (area_size * 0.25, area_size * 0.5),   # Berkeley / Richmond
            (area_size * 0.7, area_size * 0.55),   # SF Sunset / Richmond dist
            (area_size * 0.45, area_size * 0.45),  # central Bay / Alameda
        ]
        for i in range(n_valley):
            cx, cy = self.rng.choice(valley_centers)
            # Tight clusters — valley nodes are dense, within a few km of each other
            spread = area_size * 0.04
            x = max(0, min(area_size, cx + self.rng.gauss(0, spread)))
            y = max(0, min(area_size, cy + self.rng.gauss(0, spread)))

            node = Node(node_id, x, y)
            node.elevation = self.rng.uniform(0, 100)  # sea level to 100m
            node.node_tier = "valley"
            node.terrain = "urban"  # buildings, obstacles
            # 20% are indoor nodes with very short range
            if self.rng.random() < 0.2:
                node.terrain = "indoor"
                node.tx_range = int(area_size * 0.015)  # ~750m
            else:
                node.tx_range = int(area_size * 0.05)  # ~2.5km
            node.battery = self.rng.uniform(30, 90)  # handheld, variable charge
            node.sf = 7  # short range, fast
            self.nodes[node_id] = node
            node_id += 1

    def _create_links(self):
        """Create links between nodes within range.

        Supports per-node TX ranges (for tiered topologies like Bay Area).
        Link exists if either node can reach the other — but quality is
        asymmetric based on each node's terrain and effective range.
        """
        node_list = list(self.nodes.values())
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                na = node_list[i]
                nb = node_list[j]
                dist = na.distance_to(nb)

                # Use per-node TX range if set, otherwise network default
                range_a = na.tx_range if na.tx_range > 0 else self.lora_range
                range_b = nb.tx_range if nb.tx_range > 0 else self.lora_range

                # Link exists if at least one node can reach the other
                if dist > max(range_a, range_b):
                    continue

                # Compute asymmetric quality based on each node's terrain
                # A->B quality depends on A's TX power/terrain reaching B
                terrain_ab = na.terrain  # TX from A uses A's terrain
                terrain_ba = nb.terrain  # TX from B uses B's terrain

                quality_ab = link_quality_from_distance(dist, terrain=terrain_ab)
                quality_ba = link_quality_from_distance(dist, terrain=terrain_ba)

                # If node can't reach (beyond its own TX range), heavily penalize
                if dist > range_a:
                    quality_ab *= 0.05  # very weak — only occasional reception
                if dist > range_b:
                    quality_ba *= 0.05

                # Apply random asymmetry on top
                if self.asymmetry > 0:
                    asym = self.rng.uniform(-self.asymmetry, self.asymmetry)
                    quality_ab = max(0.01, min(1.0, quality_ab * (1.0 + asym)))
                    quality_ba = max(0.01, min(1.0, quality_ba * (1.0 - asym)))

                if max(quality_ab, quality_ba) > 0.01:
                    # Create link with pre-computed asymmetric qualities
                    link = Link(na.id, nb.id, dist, terrain=self.terrain, asymmetry=0.0)
                    link.quality_ab = quality_ab
                    link.quality_ba = quality_ba
                    link.quality = (quality_ab + quality_ba) / 2
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
                asym = self.rng.uniform(-self.asymmetry, self.asymmetry) if self.asymmetry > 0 else 0.0
                link = Link(a_id, b_id, best_dist, terrain=self.terrain, asymmetry=asym)
                # Force minimum quality for connectivity links
                link.quality = max(link.quality, 0.1)
                link.quality_ab = max(link.quality_ab, 0.1)
                link.quality_ba = max(link.quality_ba, 0.1)
                self.links.append(link)
                key = (min(a_id, b_id), max(a_id, b_id))
                self.link_map[key] = link
                self.nodes[a_id].neighbors[b_id] = link.quality_ab
                self.nodes[b_id].neighbors[a_id] = link.quality_ba

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

        # Remove old links involving moved nodes (filter in one pass, O(L))
        kept_links = []
        for link in self.links:
            if link.node_a in node_set or link.node_b in node_set:
                key = (min(link.node_a, link.node_b), max(link.node_a, link.node_b))
                self.link_map.pop(key, None)
                # Remove neighbor references
                na = self.nodes[link.node_a]
                nb = self.nodes[link.node_b]
                na.neighbors.pop(link.node_b, None)
                nb.neighbors.pop(link.node_a, None)
            else:
                kept_links.append(link)
        self.links = kept_links

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

        # If only 1 cluster and many nodes, subdivide by quadrant
        if len(self.clusters) == 1 and len(self.nodes) > 10:
            self._subdivide_by_quadrant()
        else:
            # Subdivide any oversized clusters (>50 nodes)
            self._subdivide_large_clusters()

    def _subdivide_by_quadrant(self):
        """Recursively subdivide clusters until each has <= MAX_CLUSTER_SIZE nodes."""
        MAX_CLUSTER_SIZE = 50

        def _split_region(node_ids, x_min, x_max, y_min, y_max, depth=0):
            """Recursively split a region into quadrants if too large."""
            if len(node_ids) <= MAX_CLUSTER_SIZE or depth > 5:
                return [node_ids]

            mid_x = (x_min + x_max) / 2.0
            mid_y = (y_min + y_max) / 2.0
            quadrants = [[], [], [], []]  # TL, TR, BL, BR

            for nid in node_ids:
                node = self.nodes[nid]
                qx = 0 if node.x < mid_x else 1
                qy = 0 if node.y < mid_y else 2
                quadrants[qx + qy].append(nid)

            result = []
            bounds = [
                (x_min, mid_x, y_min, mid_y),
                (mid_x, x_max, y_min, mid_y),
                (x_min, mid_x, mid_y, y_max),
                (mid_x, x_max, mid_y, y_max),
            ]
            for q, (bx0, bx1, by0, by1) in zip(quadrants, bounds):
                if q:
                    result.extend(_split_region(q, bx0, bx1, by0, by1, depth + 1))
            return result

        all_ids = list(self.nodes.keys())
        groups = _split_region(all_ids, 0, self.area_size, 0, self.area_size)

        self.clusters = {}
        for idx, members in enumerate(groups):
            if not members:
                continue
            cluster = Cluster(idx, f"C{idx}")
            cluster.members = members
            self.clusters[idx] = cluster
            for nid in members:
                self.nodes[nid].cluster_id = idx

    def _subdivide_large_clusters(self):
        """Split clusters with >50 nodes into spatial sub-clusters."""
        MAX_CLUSTER_SIZE = 50
        new_clusters = {}
        next_id = 0

        for cluster in list(self.clusters.values()):
            if len(cluster.members) <= MAX_CLUSTER_SIZE:
                cluster_copy = Cluster(next_id, cluster.geohash_prefix)
                cluster_copy.members = cluster.members
                new_clusters[next_id] = cluster_copy
                for nid in cluster.members:
                    self.nodes[nid].cluster_id = next_id
                next_id += 1
            else:
                # Split by median x/y alternating
                members = cluster.members[:]
                splits = [members]
                depth = 0
                while any(len(s) > MAX_CLUSTER_SIZE for s in splits) and depth < 5:
                    new_splits = []
                    for s in splits:
                        if len(s) <= MAX_CLUSTER_SIZE:
                            new_splits.append(s)
                        else:
                            use_x = (depth % 2 == 0)
                            key = (lambda nid: self.nodes[nid].x) if use_x else (lambda nid: self.nodes[nid].y)
                            s.sort(key=key)
                            mid = len(s) // 2
                            new_splits.append(s[:mid])
                            new_splits.append(s[mid:])
                    splits = new_splits
                    depth += 1

                for sub_members in splits:
                    if not sub_members:
                        continue
                    sub = Cluster(next_id, f"{cluster.geohash_prefix}.{next_id}")
                    sub.members = sub_members
                    new_clusters[next_id] = sub
                    for nid in sub_members:
                        self.nodes[nid].cluster_id = next_id
                    next_id += 1

        self.clusters = new_clusters

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

    def compute_routes(self, max_routes=5, max_hops=None):
        """Compute multi-path routes using BFS for all node pairs.

        For large networks (>200 nodes), uses lazy route computation
        to avoid O(N^2) upfront cost. Routes are computed on first access.

        Args:
            max_routes: Maximum routes to store per destination
            max_hops: Maximum hops per route (auto-scaled if None)
        """
        if max_hops is None:
            # Scale with network size: sqrt(n) but at least 15, at most 40
            import math
            max_hops = max(15, min(40, int(math.sqrt(len(self.nodes)) * 1.5)))
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

    def _dynamic_max_hops(self):
        """Compute dynamic max hops based on network size: clamp(sqrt(n)*3, 15, 40)."""
        return max(15, min(40, int(math.sqrt(len(self.nodes)) * 3)))

    def _bfs_shortest_path(self, src_id, dst_id, exclude=None):
        """Public BFS shortest path, avoiding excluded nodes. Used for emergency re-routing."""
        max_hops = self._dynamic_max_hops()
        return self._bfs_path(src_id, dst_id, max_hops, exclude or set())

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

    def compute_silencing(self, silence_fraction=0.6, rotation_interval=600.0):
        """Compute which nodes should be silenced to reduce network noise.

        Silenced nodes still listen (receive OGMs, accept direct messages)
        but do NOT rebroadcast or send OGMs. The network still knows they exist.

        Algorithm:
        1. Compute redundancy score for each node (how replaceable it is)
        2. Compute silence priority = redundancy × (1 - battery)
        3. Silence the top N% of nodes by priority within each cluster
        4. Never silence: border nodes, nodes on active routing paths,
           nodes that are the only path to a neighbor

        Args:
            silence_fraction: target fraction of redundant nodes to silence (0-1)
            rotation_interval: seconds between rotation cycles
        """
        for node in self.nodes.values():
            if node.battery <= 0:
                continue

            # ── Step 1: Compute redundancy score ──
            # A node is redundant if ALL its neighbors can also be reached
            # by other nodes (i.e., removing this node doesn't disconnect anyone)

            if len(node.neighbors) == 0:
                node.redundancy_score = 0.0
                node.silence_priority = 0.0
                node.silent = False
                continue

            # Check redundancy based on actual path criticality:
            # 1. How many of my neighbors have plenty of alternative connections?
            # 2. Am I the ONLY bridge between two clusters? (critical border)
            n_neighbors = len(node.neighbors)
            n_redundant_neighbors = 0

            for neighbor_id in node.neighbors:
                neighbor = self.nodes.get(neighbor_id)
                if not neighbor or neighbor.battery <= 0:
                    n_redundant_neighbors += 1
                    continue
                # Does this neighbor have at least 2 other alive neighbors?
                other_connections = sum(
                    1 for nid in neighbor.neighbors
                    if nid != node.id and nid in self.nodes
                    and self.nodes[nid].battery > 0
                )
                if other_connections >= 2:
                    n_redundant_neighbors += 1

            node.redundancy_score = n_redundant_neighbors / n_neighbors

            # Critical border check: penalize nodes that are rare bridges
            if node.is_border and node.cluster_id is not None:
                my_cid = node.cluster_id
                cluster_obj = self.clusters.get(my_cid)
                if cluster_obj:
                    n_border_in_cluster = sum(
                        1 for bid in cluster_obj.border_nodes
                        if bid in self.nodes and self.nodes[bid].battery > 0
                    )
                    if n_border_in_cluster <= 3:
                        # Very few border nodes — this one is critical
                        node.redundancy_score *= 0.1
                    elif n_border_in_cluster <= 6:
                        node.redundancy_score *= 0.5

            # ── Step 2: Compute silence priority ──
            # Higher = more likely to be silenced
            # Low battery → higher priority (save the battery!)
            # High redundancy → higher priority (safe to silence)
            battery_factor = 1.0 - node.battery_score()  # 0=full, 1=empty
            node.silence_priority = node.redundancy_score * 0.6 + battery_factor * 0.4

        # ── Step 3: Apply silencing per cluster ──
        for cluster in self.clusters.values():
            # Get alive, non-border members sorted by silence priority
            candidates = []
            for nid in cluster.members:
                n = self.nodes[nid]
                if n.battery <= 0:
                    continue
                if n.redundancy_score < 0.5:
                    # Not redundant enough — keep active
                    continue
                candidates.append(n)

            candidates.sort(key=lambda n: n.silence_priority, reverse=True)

            # Silence the top fraction
            n_to_silence = int(len(candidates) * silence_fraction)
            for i, n in enumerate(candidates):
                if i < n_to_silence:
                    n.silent = True
                    n.silence_until = self.sim_time + rotation_interval
                else:
                    n.silent = False

    def rotate_silencing(self, silence_fraction=0.6, rotation_interval=600.0):
        """Rotate which nodes are silenced (battery-fair scheduling).

        Called periodically (every rotation_interval). Re-evaluates
        redundancy scores (nodes may have moved, died, or changed load)
        and rotates the silent set so batteries drain evenly.
        """
        # Wake up all expired silences
        for node in self.nodes.values():
            if node.silent and self.sim_time >= node.silence_until:
                node.silent = False

        # Recompute with fresh data
        self.compute_silencing(silence_fraction, rotation_interval)

    def get_silencing_stats(self):
        """Return statistics about current silencing state."""
        alive = [n for n in self.nodes.values() if n.battery > 0]
        silent = [n for n in alive if n.silent]
        active = [n for n in alive if not n.silent]
        return {
            "alive": len(alive),
            "silent": len(silent),
            "active": len(active),
            "silence_pct": round(len(silent) / max(len(alive), 1) * 100, 1),
            "avg_redundancy": round(
                sum(n.redundancy_score for n in alive) / max(len(alive), 1), 2
            ),
            "silent_by_tier": {
                tier: sum(1 for n in silent if n.node_tier == tier)
                for tier in set(n.node_tier for n in silent)
            } if silent else {},
            "active_by_tier": {
                tier: sum(1 for n in active if n.node_tier == tier)
                for tier in set(n.node_tier for n in active)
            },
        }

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

        result = {
            "nodes": n_nodes,
            "links": n_links,
            "clusters": n_clusters,
            "avg_neighbors": round(avg_neighbors, 1),
            "avg_routes_per_dest": round(avg_routes, 1),
        }

        # Add tier breakdown if any nodes have non-default tiers
        tier_counts = defaultdict(int)
        for node in self.nodes.values():
            tier_counts[node.node_tier] += 1
        if len(tier_counts) > 1 or "valley" not in tier_counts:
            result["tiers"] = dict(tier_counts)

        return result

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
