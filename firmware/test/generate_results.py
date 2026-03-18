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
    def __init__(self, nodes, max_range=70000):
        self.nodes = {n.id: n for n in nodes}; self.max_range = max_range; self.total_tx = 0

    def deliver_ogm(self, sid, pkt_bytes):
        pkt = wire_unpack(pkt_bytes)
        for n in self.nodes.values():
            if n.id == sid: continue
            d = self.nodes[sid].distance_to(n)
            if d > self.max_range: continue
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

# Create network
nodes = [
    VNode(0, 48.130, 11.580, 95), VNode(1, 48.132, 11.582, 90),
    VNode(2, 48.134, 11.584, 85), VNode(3, 48.128, 11.580, 88),
    VNode(4, 48.130, 11.582, 92), VNode(5, 48.133, 11.590, 80),
    VNode(6, 48.370, 10.900, 87), VNode(7, 48.372, 10.905, 91),
    VNode(8, 48.365, 10.895, 83), VNode(9, 48.368, 10.910, 89),
]

random.seed(42)
lora = VLoRa(nodes, max_range=70000)
for r in range(3):
    for n in nodes: lora.deliver_ogm(n.id, n.create_ogm())
for n in nodes: n.build_routes(nodes)

# Run 20 messages
random.seed(42)
messages = []
for i in range(20):
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

node_info = [{'id':n.id,'lat':n.lat,'lon':n.lon,'cluster':n.cluster,'geohash':n.geohash,
              'is_border':n.is_border,'battery':n.battery,'neighbors':sorted(n.neighbors.keys()),
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
        'total_messages': 20, 's5_delivered': s5d,
        's5_delivery_rate': round(s5d/20*100, 1),
        's5_total_tx': s5tx, 's5_avg_tx': round(s5tx/max(s5d,1), 1),
        's5_avg_hops': round(sum(m['s5_hops'] for m in messages if m['s5_delivered'])/max(s5d,1), 1),
        'flood_total_tx': ftx, 'flood_avg_tx': round(ftx/20, 1),
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
