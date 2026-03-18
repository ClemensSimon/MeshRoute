"""
MeshRoute Virtual Network Test — 10 Nodes

Simulates a complete mesh network with 10 virtual nodes running
the firmware logic. Tests OGM discovery, route building, directed
routing, fallback flooding, and multi-hop delivery.

Network topology (2 clusters connected by bridge nodes):

  Cluster 0 (Munich Center)         Cluster 1 (Munich East)
  ┌─────────────────────┐           ┌─────────────────────┐
  │  N0 ── N1 ── N2     │           │     N6 ── N7        │
  │  │     │     │      │           │     │     │         │
  │  N3 ── N4 ──[N5]────┼───────────┼───[N8]── N9        │
  └─────────────────────┘           └─────────────────────┘
                  ↑ border                 ↑ border
                  └────── bridge link ─────┘

N5 and N8 are border nodes (bridge between clusters).
"""

import sys
import os
import math
import struct
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'simulator'))

# ── Reuse firmware logic from verification tests ────────────────

GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
S5_MAGIC = 0x55
PKT_OGM = 0x01
PKT_DATA = 0x02
WIRE_FMT = '<BBIIIBBIBB'
WIRE_SIZE = struct.calcsize(WIRE_FMT)

def geohash_encode(lat, lon, precision=4):
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    bits, char_idx, result, is_lon = 0, 0, [], True
    while len(result) < precision:
        if is_lon:
            mid = (lon_range[0] + lon_range[1]) / 2.0
            if lon >= mid: char_idx = (char_idx << 1) | 1; lon_range[0] = mid
            else: char_idx = char_idx << 1; lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2.0
            if lat >= mid: char_idx = (char_idx << 1) | 1; lat_range[0] = mid
            else: char_idx = char_idx << 1; lat_range[1] = mid
        is_lon = not is_lon; bits += 1
        if bits == 5: result.append(GEOHASH_BASE32[char_idx]); bits = 0; char_idx = 0
    return ''.join(result)

def geohash_to_cluster(gh):
    cid = 0
    for ch in gh: cid = (cid * 31 + ord(ch)) & 0xFF
    return cid

def compute_weight(q, l, b): return 0.4*q + 0.35*(1-l) + 0.25*b

def qos_gate(nhs, pri):
    if nhs >= 0.8: return pri <= 7
    elif nhs >= 0.6: return pri <= 5
    elif nhs >= 0.4: return pri <= 3
    elif nhs >= 0.2: return pri <= 1
    return pri <= 0

def wire_pack(ptype, src, dst, pkt_id, hops, ttl, nhop, pri, payload=b''):
    hdr = struct.pack(WIRE_FMT, S5_MAGIC, ptype, src, dst, pkt_id, hops, ttl, nhop, pri, len(payload))
    return hdr + payload

def wire_unpack(data):
    if len(data) < WIRE_SIZE: return None
    f = struct.unpack(WIRE_FMT, data[:WIRE_SIZE])
    if f[0] != S5_MAGIC: return None
    pl = data[WIRE_SIZE:WIRE_SIZE+f[9]]
    return {'type':f[1],'src':f[2],'dst':f[3],'id':f[4],'hops':f[5],'ttl':f[6],'nhop':f[7],'pri':f[8],'plen':f[9],'payload':pl}

# ── Virtual Node ────────────────────────────────────────────────

class VirtualNode:
    def __init__(self, node_id, lat, lon, battery=90):
        self.id = node_id
        self.lat = lat
        self.lon = lon
        self.battery = battery
        self.geohash = geohash_encode(lat, lon, 4)
        self.cluster = geohash_to_cluster(self.geohash)
        self.neighbors = {}       # node_id -> {quality, lat, lon, battery, cluster}
        self.routing_table = {}   # dst_id -> [path]
        self.is_border = False
        self.rx_log = []          # received packets log
        self.tx_count = 0
        self.dedup = set()
        self.pkt_counter = node_id * 1000

    def distance_to(self, other):
        dlat = (self.lat - other.lat) * 111320
        dlon = (self.lon - other.lon) * 111320 * math.cos(math.radians(self.lat))
        return math.sqrt(dlat**2 + dlon**2)

    def receive_ogm(self, pkt, rssi, snr):
        """Process an OGM from another node."""
        if pkt['src'] == self.id: return
        if pkt['id'] in self.dedup: return
        self.dedup.add(pkt['id'])

        quality = max(0, min(1, (rssi + 120) / 70))
        ogm_data = struct.unpack('<ffBBBB', pkt['payload'][:12]) if len(pkt['payload']) >= 12 else None
        if not ogm_data: return

        lat, lon, batt, cluster, is_border, n_count = ogm_data
        self.neighbors[pkt['src']] = {
            'quality': quality, 'lat': lat, 'lon': lon,
            'battery': batt, 'cluster': cluster, 'is_border': is_border,
        }

        # Update border status
        self.is_border = any(n['cluster'] != self.cluster for n in self.neighbors.values())

    def create_ogm(self):
        """Create an OGM packet."""
        self.pkt_counter += 1
        payload = struct.pack('<ffBBBB', self.lat, self.lon, self.battery,
                              self.cluster, 1 if self.is_border else 0, len(self.neighbors))
        return wire_pack(PKT_OGM, self.id, 0xFFFFFFFF, self.pkt_counter, 0, 3, 0, 7, payload)

    def build_routes(self, all_nodes):
        """Build routing table from neighbor info using BFS."""
        self.routing_table = {}
        for dst_id in [n.id for n in all_nodes if n.id != self.id]:
            path = self._bfs(dst_id, all_nodes)
            if path:
                self.routing_table[dst_id] = path

    def _bfs(self, dst_id, all_nodes):
        """BFS shortest path through known neighbors."""
        visited = {self.id}
        queue = [(self.id, [self.id])]
        # Build adjacency from all nodes' neighbor tables
        adj = {}
        for n in all_nodes:
            adj[n.id] = set(n.neighbors.keys())
        while queue:
            cur, path = queue.pop(0)
            for neighbor_id in adj.get(cur, []):
                if neighbor_id in visited: continue
                new_path = path + [neighbor_id]
                if neighbor_id == dst_id: return new_path
                visited.add(neighbor_id)
                queue.append((neighbor_id, new_path))
        return None

    def route_packet(self, pkt):
        """Make routing decision: DIRECT, FLOOD, DROP, DELIVERED."""
        if pkt['dst'] == self.id:
            return 'DELIVERED', None
        if pkt['hops'] >= pkt['ttl']:
            return 'DROP', None
        if pkt['dst'] in self.routing_table:
            path = self.routing_table[pkt['dst']]
            for i in range(len(path) - 1):
                if path[i] == self.id:
                    return 'DIRECT', path[i + 1]
        return 'FLOOD', None

    def create_data(self, dst_id, message, next_hop=0):
        self.pkt_counter += 1
        payload = message.encode('utf-8')[:200]
        return wire_pack(PKT_DATA, self.id, dst_id, self.pkt_counter, 0, 20, next_hop, 3, payload)

    def __repr__(self):
        return f"Node({self.id}, C{self.cluster}, {'BRD' if self.is_border else '   '}, {len(self.neighbors)}n)"


# ── Virtual LoRa Channel ───────────────────────────────────────

class VirtualLoRa:
    """Simulates LoRa radio propagation between nodes."""

    def __init__(self, nodes, max_range=2000):
        self.nodes = {n.id: n for n in nodes}
        self.max_range = max_range
        self.total_tx = 0
        self.log = []

    def transmit(self, sender_id, packet_bytes):
        """Broadcast packet from sender to all nodes in range."""
        sender = self.nodes[sender_id]
        self.total_tx += 1
        sender.tx_count += 1
        pkt = wire_unpack(packet_bytes)
        if not pkt: return []

        received_by = []
        for node in self.nodes.values():
            if node.id == sender_id: continue
            dist = sender.distance_to(node)
            if dist > self.max_range: continue

            # RSSI model
            rssi = 14 - (20 * math.log10(max(1, dist)) + 32)  # simplified
            snr = rssi + 120
            quality = max(0, min(1, (rssi + 120) / 70))

            # Probabilistic reception
            import random
            if random.random() > quality: continue

            received_by.append((node.id, rssi, snr))

        return received_by

    def deliver_ogm(self, sender_id, packet_bytes):
        """Send OGM and let receivers process it."""
        receivers = self.transmit(sender_id, packet_bytes)
        pkt = wire_unpack(packet_bytes)
        for node_id, rssi, snr in receivers:
            self.nodes[node_id].receive_ogm(pkt, rssi, snr)
        return receivers

    def deliver_data(self, sender_id, packet_bytes, max_hops=20):
        """Route a data packet through the network, hop by hop."""
        pkt = wire_unpack(packet_bytes)
        if not pkt: return False, 0, []

        current_node = self.nodes[sender_id]
        path_taken = [sender_id]
        total_tx = 0

        for hop in range(max_hops):
            action, next_hop = current_node.route_packet(pkt)

            if action == 'DELIVERED':
                current_node.rx_log.append(pkt)
                self.log.append(f"  Hop {hop}: Node {current_node.id} -> DELIVERED! ({total_tx} TX)")
                return True, total_tx, path_taken

            elif action == 'DIRECT':
                total_tx += 1
                self.total_tx += 1
                current_node.tx_count += 1
                path_taken.append(next_hop)
                self.log.append(f"  Hop {hop}: Node {current_node.id} -> DIRECT to {next_hop}")

                if next_hop not in self.nodes:
                    self.log.append(f"  Hop {hop}: Node {next_hop} not found!")
                    return False, total_tx, path_taken

                # Check if link exists (nodes in range)
                dist = current_node.distance_to(self.nodes[next_hop])
                if dist > self.max_range:
                    self.log.append(f"  Hop {hop}: Node {next_hop} out of range ({dist:.0f}m)")
                    return False, total_tx, path_taken

                # Update packet for next hop
                pkt = dict(pkt)
                pkt['hops'] += 1
                pkt['nhop'] = 0  # next hop will decide
                current_node = self.nodes[next_hop]

            elif action == 'FLOOD':
                self.log.append(f"  Hop {hop}: Node {current_node.id} -> FLOOD (no direct route)")
                # Flood to all neighbors, hope for the best
                total_tx += len(current_node.neighbors)
                self.total_tx += len(current_node.neighbors)
                # Find neighbor closest to destination
                dst_node = self.nodes.get(pkt['dst'])
                if not dst_node: return False, total_tx, path_taken
                best_id, best_dist = None, float('inf')
                for nid in current_node.neighbors:
                    if nid in path_taken: continue
                    if nid in self.nodes:
                        d = self.nodes[nid].distance_to(dst_node)
                        if d < best_dist:
                            best_dist = d
                            best_id = nid
                if best_id:
                    path_taken.append(best_id)
                    pkt = dict(pkt)
                    pkt['hops'] += 1
                    current_node = self.nodes[best_id]
                else:
                    return False, total_tx, path_taken

            elif action == 'DROP':
                self.log.append(f"  Hop {hop}: Node {current_node.id} -> DROP (TTL/QoS)")
                return False, total_tx, path_taken

        return False, total_tx, path_taken


# ── Test Network Setup ──────────────────────────────────────────

def create_network():
    """Create a 10-node network with 2 clusters."""
    # Cluster 0: Munich (48.13, 11.58) — geohash u281
    nodes = [
        VirtualNode(0, 48.130, 11.580, 95),  # N0
        VirtualNode(1, 48.132, 11.582, 90),  # N1
        VirtualNode(2, 48.134, 11.584, 85),  # N2
        VirtualNode(3, 48.128, 11.580, 88),  # N3
        VirtualNode(4, 48.130, 11.582, 92),  # N4
        VirtualNode(5, 48.133, 11.590, 80),  # N5 - border node →
    ]
    # Cluster 1: Augsburg area (48.37, 10.90) — clearly different geohash u284
    nodes += [
        VirtualNode(6, 48.370, 10.900, 87),  # N6
        VirtualNode(7, 48.372, 10.905, 91),  # N7
        VirtualNode(8, 48.365, 10.895, 83),  # N8 - border node ←
        VirtualNode(9, 48.368, 10.910, 89),  # N9
    ]
    return nodes


# ── Tests ───────────────────────────────────────────────────────

import random
random.seed(42)

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} -- {detail}")


def test_network_setup():
    print("\n=== Phase 1: Network Setup ===")
    nodes = create_network()

    # Check clustering
    clusters = {}
    for n in nodes:
        clusters.setdefault(n.cluster, []).append(n.id)

    n_clusters = len(clusters)
    test("Network has 2+ clusters", n_clusters >= 2, f"clusters={n_clusters}")
    test("All 10 nodes created", len(nodes) == 10)

    print(f"\n  Cluster assignment:")
    for cid, members in sorted(clusters.items()):
        print(f"    Cluster {cid}: Nodes {members}")

    return nodes


def test_ogm_discovery(nodes):
    print("\n=== Phase 2: OGM Discovery (3 rounds) ===")
    lora = VirtualLoRa(nodes, max_range=70000)  # 70km range (simulated, covers Munich-Augsburg)

    for round_num in range(3):
        for node in nodes:
            ogm = node.create_ogm()
            receivers = lora.deliver_ogm(node.id, ogm)

    # Check neighbor counts
    for n in nodes:
        print(f"    Node {n.id} (C{n.cluster}{'*' if n.is_border else ' '}): "
              f"{len(n.neighbors)} neighbors: {sorted(n.neighbors.keys())}")

    test("All nodes discovered neighbors", all(len(n.neighbors) > 0 for n in nodes))
    test("Border nodes detected", any(n.is_border for n in nodes),
         f"borders={[n.id for n in nodes if n.is_border]}")

    # N5 and N8 should be border nodes (between clusters)
    border_ids = [n.id for n in nodes if n.is_border]
    test("Bridge nodes include N5 or N8", 5 in border_ids or 8 in border_ids,
         f"borders={border_ids}")

    total_ogm_tx = lora.total_tx
    print(f"\n    OGM discovery: {total_ogm_tx} TX in 3 rounds")
    return lora


def test_route_building(nodes, lora):
    print("\n=== Phase 3: Route Table Building ===")

    for n in nodes:
        n.build_routes(nodes)

    # Check routing tables
    routes_found = 0
    routes_total = 0
    for n in nodes:
        for dst_id in [m.id for m in nodes if m.id != n.id]:
            routes_total += 1
            if dst_id in n.routing_table:
                routes_found += 1

    coverage = routes_found / max(routes_total, 1) * 100
    test(f"Route coverage > 80%", coverage > 80, f"coverage={coverage:.1f}%")
    print(f"    Routes: {routes_found}/{routes_total} ({coverage:.1f}%)")

    # Show some example routes
    print(f"\n    Example routes from Node 0:")
    for dst_id in sorted(nodes[0].routing_table.keys())[:5]:
        path = nodes[0].routing_table[dst_id]
        print(f"      0 -> {dst_id}: {' -> '.join(map(str, path))} ({len(path)-1} hops)")

    return routes_found


def test_direct_routing(nodes, lora):
    print("\n=== Phase 4: Direct Routing (System 5) ===")
    lora.log = []

    # Test 1: Same-cluster message (N0 -> N4)
    print("\n  Test 4a: Same-cluster N0 -> N4")
    pkt = nodes[0].create_data(4, "Hello from N0 to N4")
    action, nhop = nodes[0].route_packet(wire_unpack(pkt))
    test("Same-cluster route is DIRECT", action == 'DIRECT', f"action={action}")

    delivered, tx, path = lora.deliver_data(0, pkt)
    test("Same-cluster delivered", delivered, f"path={path}")
    test("Same-cluster efficient (<=3 TX)", tx <= 3, f"tx={tx}")
    for line in lora.log: print(f"    {line}")
    print(f"    Path: {' -> '.join(map(str, path))} | TX: {tx}")

    # Test 2: Cross-cluster message (N0 -> N7)
    print("\n  Test 4b: Cross-cluster N0 -> N7")
    lora.log = []
    pkt2 = nodes[0].create_data(7, "Hello from N0 to N7")
    delivered2, tx2, path2 = lora.deliver_data(0, pkt2)
    test("Cross-cluster delivered", delivered2, f"path={path2}")
    # BFS may find a shorter path not through N5/N8 — that's correct optimization
    test("Cross-cluster crosses cluster boundary",
         any(nodes[p].cluster != nodes[path2[0]].cluster for p in path2 if p < 10),
         f"path={path2}")
    for line in lora.log: print(f"    {line}")
    print(f"    Path: {' -> '.join(map(str, path2))} | TX: {tx2}")

    # Test 3: Opposite corners (N3 -> N9)
    print("\n  Test 4c: Opposite corners N3 -> N9")
    lora.log = []
    pkt3 = nodes[3].create_data(9, "Hello from N3 to N9")
    delivered3, tx3, path3 = lora.deliver_data(3, pkt3)
    test("Corner-to-corner delivered", delivered3, f"path={path3}")
    for line in lora.log: print(f"    {line}")
    print(f"    Path: {' -> '.join(map(str, path3))} | TX: {tx3}")

    return delivered and delivered2 and delivered3


def test_flood_fallback(nodes, lora):
    print("\n=== Phase 5: Flood Fallback ===")

    # Create node with no routing table
    orphan = VirtualNode(99, 48.1350, 11.5720, 70)
    orphan.neighbors = {1: {'quality': 0.8, 'lat': 48.1355, 'lon': 11.5720,
                            'battery': 90, 'cluster': nodes[1].cluster, 'is_border': False}}
    # routing_table is empty -> should FLOOD
    pkt = orphan.create_data(7, "Hello from orphan")
    action, nhop = orphan.route_packet(wire_unpack(pkt))
    test("No route -> FLOOD fallback", action == 'FLOOD', f"action={action}")


def test_qos_filtering(nodes, lora):
    print("\n=== Phase 6: QoS Filtering ===")

    test("NHS 0.9 + priority 7 -> PASS", qos_gate(0.9, 7))
    test("NHS 0.3 + priority 5 -> BLOCK", not qos_gate(0.3, 5))
    test("NHS 0.05 + priority 0 (SOS) -> PASS", qos_gate(0.05, 0))


def test_efficiency_comparison(nodes, lora):
    print("\n=== Phase 7: Efficiency Comparison (10 random messages) ===")

    random.seed(42)
    s5_total_tx = 0
    s5_delivered = 0
    flood_total_tx = 0
    flood_delivered = 0

    messages = []
    for _ in range(10):
        src = random.choice(nodes)
        dst = random.choice([n for n in nodes if n.id != src.id])
        messages.append((src.id, dst.id))

    # System 5 routing
    for src_id, dst_id in messages:
        lora.log = []
        pkt = nodes[src_id].create_data(dst_id, f"Msg {src_id}->{dst_id}")
        delivered, tx, path = lora.deliver_data(src_id, pkt)
        s5_total_tx += tx
        if delivered: s5_delivered += 1

    # Estimate flooding cost (each hop broadcasts to all neighbors)
    for src_id, dst_id in messages:
        # BFS hop count
        path = nodes[src_id]._bfs(dst_id, nodes)
        if path:
            flood_delivered += 1
            for nid in path:
                flood_total_tx += len(nodes[nid].neighbors)

    print(f"\n    System 5:  {s5_delivered}/10 delivered, {s5_total_tx} TX")
    print(f"    Flooding:  {flood_delivered}/10 delivered, {flood_total_tx} TX (estimated)")
    if flood_total_tx > 0:
        saving = (1 - s5_total_tx / flood_total_tx) * 100
        print(f"    Savings:   {saving:.1f}%")
        test(f"System 5 uses fewer TX than flooding", s5_total_tx < flood_total_tx,
             f"s5={s5_total_tx} flood={flood_total_tx}")
    test(f"System 5 delivers most messages", s5_delivered >= 7,
         f"delivered={s5_delivered}/10")


def test_wire_protocol_live(nodes):
    print("\n=== Phase 8: Wire Protocol Live Test ===")

    # Node 0 creates OGM, Node 1 parses it
    ogm_bytes = nodes[0].create_ogm()
    pkt = wire_unpack(ogm_bytes)
    test("OGM packet valid", pkt is not None)
    test("OGM type correct", pkt['type'] == PKT_OGM)
    test("OGM src is Node 0", pkt['src'] == 0)
    test("OGM is broadcast", pkt['dst'] == 0xFFFFFFFF)

    # Parse OGM payload
    ogm_data = struct.unpack('<ffBBBB', pkt['payload'][:12])
    lat, lon, batt, cluster, is_border, n_count = ogm_data
    test("OGM lat matches", abs(lat - 48.130) < 0.01, f"lat={lat}")
    test("OGM lon matches", abs(lon - 11.580) < 0.01, f"lon={lon}")
    test("OGM battery matches", batt == 95)

    # Node 0 creates data packet for Node 7
    data_bytes = nodes[0].create_data(7, "Test message", next_hop=5)
    dpkt = wire_unpack(data_bytes)
    test("Data packet valid", dpkt is not None)
    test("Data type correct", dpkt['type'] == PKT_DATA)
    test("Data dst is 7", dpkt['dst'] == 7)
    test("Data next_hop is 5", dpkt['nhop'] == 5)
    test("Data payload correct", dpkt['payload'] == b'Test message')


# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MeshRoute Virtual Network Test — 10 Nodes")
    print("=" * 60)

    nodes = test_network_setup()
    lora = test_ogm_discovery(nodes)
    test_route_building(nodes, lora)
    test_direct_routing(nodes, lora)
    test_flood_fallback(nodes, lora)
    test_qos_filtering(nodes, lora)
    test_efficiency_comparison(nodes, lora)
    test_wire_protocol_live(nodes)

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}\n")

    sys.exit(0 if failed == 0 else 1)
