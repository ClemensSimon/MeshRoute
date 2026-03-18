# -*- coding: utf-8 -*-
"""Generate detailed ESP virtual network test results as JSON."""

import json, sys, os, math, random, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'simulator'))

# Import from virtual network test
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

def wire_pack(ptype, src, dst, pkt_id, hops, ttl, nhop, pri, payload=b''):
    hdr = struct.pack(WIRE_FMT, S5_MAGIC, ptype, src, dst, pkt_id, hops, ttl, nhop, pri, len(payload))
    return hdr + payload

def wire_unpack(data):
    if len(data) < WIRE_SIZE: return None
    f = struct.unpack(WIRE_FMT, data[:WIRE_SIZE])
    if f[0] != S5_MAGIC: return None
    pl = data[WIRE_SIZE:WIRE_SIZE+f[9]]
    return {'type':f[1],'src':f[2],'dst':f[3],'id':f[4],'hops':f[5],'ttl':f[6],'nhop':f[7],'pri':f[8],'plen':f[9],'payload':pl}

class VNode:
    def __init__(self, nid, lat, lon, batt=90):
        self.id = nid; self.lat = lat; self.lon = lon; self.battery = batt
        self.geohash = geohash_encode(lat, lon, 4)
        self.cluster = geohash_to_cluster(self.geohash)
        self.neighbors = {}; self.routing_table = {}
        self.is_border = False; self.tx_count = 0; self.dedup = set()
        self.pkt_counter = nid * 1000

    def distance_to(self, other):
        dlat = (self.lat - other.lat) * 111320
        dlon = (self.lon - other.lon) * 111320 * math.cos(math.radians(self.lat))
        return math.sqrt(dlat**2 + dlon**2)

    def receive_ogm(self, pkt, rssi, snr):
        if pkt['src'] == self.id or pkt['id'] in self.dedup: return
        self.dedup.add(pkt['id'])
        quality = max(0, min(1, (rssi + 120) / 70))
        if len(pkt['payload']) < 12: return
        lat, lon, batt, cluster, ib, nc = struct.unpack('<ffBBBB', pkt['payload'][:12])
        self.neighbors[pkt['src']] = {'quality': quality, 'lat': lat, 'lon': lon, 'battery': batt, 'cluster': cluster, 'is_border': ib}
        self.is_border = any(n['cluster'] != self.cluster for n in self.neighbors.values())

    def create_ogm(self):
        self.pkt_counter += 1
        pl = struct.pack('<ffBBBB', self.lat, self.lon, self.battery, self.cluster, 1 if self.is_border else 0, len(self.neighbors))
        return wire_pack(PKT_OGM, self.id, 0xFFFFFFFF, self.pkt_counter, 0, 3, 0, 7, pl)

    def build_routes(self, all_nodes):
        self.routing_table = {}
        adj = {n.id: set(n.neighbors.keys()) for n in all_nodes}
        for dst_id in [n.id for n in all_nodes if n.id != self.id]:
            visited = {self.id}; queue = [(self.id, [self.id])]
            while queue:
                cur, path = queue.pop(0)
                for nid in adj.get(cur, []):
                    if nid in visited: continue
                    np = path + [nid]
                    if nid == dst_id: self.routing_table[dst_id] = np; break
                    visited.add(nid); queue.append((nid, np))
                else: continue
                break

    def route_packet(self, pkt):
        if pkt['dst'] == self.id: return 'DELIVERED', None
        if pkt['hops'] >= pkt['ttl']: return 'DROP', None
        if pkt['dst'] in self.routing_table:
            path = self.routing_table[pkt['dst']]
            for i in range(len(path)-1):
                if path[i] == self.id: return 'DIRECT', path[i+1]
        return 'FLOOD', None

    def create_data(self, dst, msg, nhop=0):
        self.pkt_counter += 1
        return wire_pack(PKT_DATA, self.id, dst, self.pkt_counter, 0, 20, nhop, 3, msg.encode()[:200])

class VLoRa:
    def __init__(self, nodes, max_range=2000):
        self.nodes = {n.id: n for n in nodes}; self.max_range = max_range; self.total_tx = 0

    def _in_range(self, a_id, b_id):
        """Check if two nodes can communicate (min of both ranges)."""
        a, b = self.nodes[a_id], self.nodes[b_id]
        d = a.distance_to(b)
        effective_range = min(getattr(a, 'lora_range', self.max_range),
                              getattr(b, 'lora_range', self.max_range))
        return d <= effective_range, d

    def deliver_ogm(self, sid, pkt_bytes):
        pkt = wire_unpack(pkt_bytes)
        for n in self.nodes.values():
            if n.id == sid: continue
            in_range, d = self._in_range(sid, n.id)
            if not in_range: continue
            rssi = 14 - (20*math.log10(max(1,d)) + 32)
            if random.random() > max(0, min(1, (rssi+120)/70)): continue
            n.receive_ogm(pkt, rssi, rssi+120)
        self.total_tx += 1

    def deliver_data(self, sid, pkt_bytes):
        pkt = wire_unpack(pkt_bytes)
        if not pkt: return False, 0, []
        cur = self.nodes[sid]; path = [sid]; tx = 0
        for hop in range(20):
            action, nhop = cur.route_packet(pkt)
            if action == 'DELIVERED': return True, tx, path
            elif action == 'DIRECT':
                tx += 1; path.append(nhop)
                if nhop not in self.nodes: return False, tx, path
                in_range, _ = self._in_range(cur.id, nhop)
                if not in_range: return False, tx, path
                pkt = dict(pkt); pkt['hops'] += 1; cur = self.nodes[nhop]
            elif action == 'FLOOD':
                tx += len(cur.neighbors)
                dst_n = self.nodes.get(pkt['dst'])
                if not dst_n: return False, tx, path
                best = min((nid for nid in cur.neighbors if nid not in path and nid in self.nodes),
                          key=lambda nid: self.nodes[nid].distance_to(dst_n), default=None)
                if not best: return False, tx, path
                path.append(best); pkt = dict(pkt); pkt['hops'] += 1; cur = self.nodes[best]
            else: return False, tx, path
        return False, tx, path

# ── Create 100-node network across Munich ──
# 5 clusters based on real Munich districts, ~2km LoRa range

MUNICH_CLUSTERS = {
    'Altstadt':    (48.137, 11.575, 15),  # center lat, lon, node count
    'Schwabing':   (48.160, 11.585, 20),
    'Haidhausen':  (48.130, 11.600, 20),
    'Sendling':    (48.118, 11.555, 20),
    'Neuhausen':   (48.155, 11.540, 25),
}

MUNICH_LANDMARKS = [
    # (id, lat, lon, name) — notable locations for labeling
    (0, 48.1371, 11.5754, 'Marienplatz'),
    (15, 48.1620, 11.5780, 'Uni/LMU'),
    (35, 48.1310, 11.6050, 'Ostbahnhof'),
    (55, 48.1190, 11.5500, 'Harras'),
    (75, 48.1530, 11.5370, 'Rotkreuzplatz'),
    (99, 48.1500, 11.5600, 'Hauptbahnhof'),
]

random.seed(42)
nodes = []
nid = 0
for district, (clat, clon, count) in MUNICH_CLUSTERS.items():
    for i in range(count):
        lat = clat + random.gauss(0, 0.006)  # ~600m std dev
        lon = clon + random.gauss(0, 0.008)
        batt = random.randint(40, 100)
        node = VNode(nid, round(lat, 6), round(lon, 6), batt)
        # 20% are rooftop nodes (3km range), 80% handheld (1.5km range)
        node.lora_range = 3000 if random.random() < 0.2 else 1500
        nodes.append(node)
        nid += 1

# Apply landmark names
NODE_NAMES = {}
for lid, llat, llon, lname in MUNICH_LANDMARKS:
    if lid < len(nodes):
        nodes[lid].lat = llat; nodes[lid].lon = llon
        NODE_NAMES[lid] = lname
# Name remaining by district
nid = 0
for district, (_, _, count) in MUNICH_CLUSTERS.items():
    for i in range(count):
        if nid not in NODE_NAMES:
            NODE_NAMES[nid] = f'{district} #{i+1}'
        nid += 1

random.seed(42)
lora = VLoRa(nodes, max_range=2000)  # 2km LoRa range — realistic urban
for r in range(3):
    for n in nodes: lora.deliver_ogm(n.id, n.create_ogm())

# ── Filter inter-cluster links to 2 best per cluster pair ──
# (same logic as web simulator — only dedicated bridge routes exist)
from collections import defaultdict
inter_by_pair = defaultdict(list)
for n in nodes:
    for nid, info in list(n.neighbors.items()):
        if info['cluster'] != n.cluster:
            pair = tuple(sorted([n.cluster, info['cluster']]))
            inter_by_pair[pair].append((n.id, nid, info['quality']))

for pair, links in inter_by_pair.items():
    # Keep top 2 by quality, delete rest
    links.sort(key=lambda x: x[2], reverse=True)
    keep = set()
    for a, b, q in links[:2]:
        keep.add((a, b))
        keep.add((b, a))
    for a, b, q in links:
        if (a, b) not in keep:
            if b in nodes[a].neighbors:
                del nodes[a].neighbors[b]

# Recompute border status
for n in nodes:
    n.is_border = any(info['cluster'] != n.cluster for info in n.neighbors.values())

print(f"Bridge links filtered: {sum(1 for n in nodes for nid, info in n.neighbors.items() if info['cluster'] != n.cluster) // 2} per direction")

for n in nodes: n.build_routes(nodes)

# Run 20 messages
random.seed(42)
messages = []
N_MESSAGES = 50
for i in range(N_MESSAGES):
    src = random.choice(nodes)
    dst = random.choice([n for n in nodes if n.id != src.id])
    d, tx, path = lora.deliver_data(src.id, src.create_data(dst.id, f'M{i}'))
    bfs = src.routing_table.get(dst.id, [])
    ftx = sum(len(nodes[nid].neighbors) for nid in bfs) if bfs else 0
    messages.append({
        'msg': i+1, 'src': src.id, 'dst': dst.id,
        'src_cluster': src.cluster, 'dst_cluster': dst.cluster,
        'cross_cluster': src.cluster != dst.cluster,
        's5_delivered': d, 's5_tx': tx, 's5_hops': len(path)-1 if d else 0,
        's5_path': [int(p) for p in path],
        'flood_tx': ftx, 'flood_hops': len(bfs)-1 if bfs else 0,
    })

node_info = [{'id':n.id,'name':NODE_NAMES.get(n.id, f'Node {n.id}'),
              'lat':n.lat,'lon':n.lon,'cluster':n.cluster,'geohash':n.geohash,
              'is_border':n.is_border,'battery':n.battery,
              'lora_range': getattr(n, 'lora_range', 2000),
              'neighbors':sorted(n.neighbors.keys()),
              'routes':len(n.routing_table)} for n in nodes]
clusters = {}
for n in nodes: clusters.setdefault(str(n.cluster), []).append(n.id)

s5d = sum(1 for m in messages if m['s5_delivered'])
s5tx = sum(m['s5_tx'] for m in messages)
ftx = sum(m['flood_tx'] for m in messages)

output = {
    'test_name': 'Virtual ESP32 Network — 10 Nodes',
    'description': 'Firmware logic running on 10 simulated ESP32 nodes with LoRa radio model',
    'nodes': node_info, 'clusters': clusters, 'messages': messages,
    'summary': {
        'total_messages': N_MESSAGES, 's5_delivered': s5d,
        's5_delivery_rate': round(s5d/N_MESSAGES*100, 1),
        's5_total_tx': s5tx, 's5_avg_tx': round(s5tx/max(s5d,1), 1),
        's5_avg_hops': round(sum(m['s5_hops'] for m in messages if m['s5_delivered'])/max(s5d,1), 1),
        'flood_total_tx': ftx, 'flood_avg_tx': round(ftx/N_MESSAGES, 1),
        'tx_savings_pct': round((1-s5tx/max(ftx,1))*100, 1),
    }
}

os.makedirs('docs', exist_ok=True)
with open(os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'esp-test-results.json'), 'w') as f:
    json.dump(output, f, indent=2)

print(json.dumps(output['summary'], indent=2))
print(f"\nMessages:")
for m in messages:
    status = 'OK' if m['s5_delivered'] else 'FAIL'
    print(f"  #{m['msg']:2d} N{m['src']}->N{m['dst']} [{status}] S5:{m['s5_tx']}TX/{m['s5_hops']}hop  Flood:{m['flood_tx']}TX  Path:{m['s5_path']}")
