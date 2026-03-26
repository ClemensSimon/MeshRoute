// MeshRoute Simulator - Unicast Routing Algorithms

// ---- BFS shortest path ----
function bfsPath(nodes, links, src, dst, skipHidden = false) {
  if (src === dst) return [src];
  const visited = new Set([src]);
  const queue = [[src, [src]]];
  while (queue.length) {
    const [cur, path] = queue.shift();
    for (const l of links) {
      if (!l.alive) continue;
      if (skipHidden && l.hidden) continue; // legacy guard, hidden links no longer exist
      let neighbor = -1;
      if (l.a === cur) neighbor = l.b;
      else if (l.b === cur) neighbor = l.a;
      else continue;
      if (visited.has(neighbor)) continue;
      if (nodes[neighbor].battery <= 0) continue;
      const newPath = [...path, neighbor];
      if (neighbor === dst) return newPath;
      visited.add(neighbor);
      queue.push([neighbor, newPath]);
    }
  }
  return null;
}

// ---- Routing Simulators (return rich explanation data) ----
function simulateManagedFlood(net, src, dst, rng, hopLimit = 7) {
  const txEvents = []; // [{from, to, time, hop}]
  const seen = new Set([src]);
  const rebroadcast = new Set([src]);
  const queue = [[src, 0]]; // [nodeId, hop]
  let delivered = false;
  let tick = 0;
  let suppressed = 0;
  let failedRx = 0;
  let droppedByLimit = 0;
  const nodesReached = new Set([src]);

  while (queue.length) {
    const [cur, hop] = queue.shift();
    if (hop >= 15) continue;

    // Hop limit enforcement — Meshtastic drops packets beyond this
    if (hop >= hopLimit) {
      droppedByLimit++;
      continue;
    }

    const node = net.nodes[cur];

    // Suppression check (non-source)
    if (cur !== src) {
      const neighborRebroadcasted = Object.keys(node.neighbors).some(
        nid => rebroadcast.has(+nid) && +nid !== src && seen.has(+nid)
      );
      if (neighborRebroadcasted && rng.next() < 0.4) {
        suppressed++;
        continue;
      }
    }

    rebroadcast.add(cur);

    for (const [nidStr, quality] of Object.entries(node.neighbors)) {
      const nid = +nidStr;
      const link = net.links.find(l => l.alive &&
        ((l.a===cur && l.b===nid) || (l.a===nid && l.b===cur)));
      if (!link) continue;

      txEvents.push({ from: cur, to: nid, time: tick++, hop: hop });

      if (rng.next() > quality) { failedRx++; continue; }
      if (seen.has(nid)) continue;
      seen.add(nid);
      nodesReached.add(nid);

      if (nid === dst) { delivered = true; continue; }
      if (net.nodes[nid].battery <= 0) continue;
      queue.push([nid, hop + 1]);
    }
  }
  return {
    txEvents, delivered, totalTx: txEvents.length,
    suppressed, failedRx, droppedByLimit,
    nodesReached: nodesReached.size,
    rebroadcasters: rebroadcast.size,
    hopLimit,
  };
}

function simulateSystem5(net, src, dst, rng) {
  // Try primary path first, then alternative, then scoped fallback flooding
  const MAX_RETRIES = 3;

  // Try up to 3 different paths
  const triedPaths = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    const excluded = new Set();
    // Exclude intermediate nodes of previously failed paths
    for (const fp of triedPaths) {
      for (let k = 1; k < fp.length - 1; k++) excluded.add(fp[k]);
    }
    const path = bfsPathExcluding(net.nodes, net.links, src, dst, excluded);
    if (!path) break;
    triedPaths.push(path);

    const txEvents = [];
    let tick = 0, delivered = true, retries = 0, failHop = -1;

    for (let i = 0; i < path.length - 1; i++) {
      const from = path[i], to = path[i+1];
      txEvents.push({ from, to, time: tick++ });
      const link = net.links.find(l => l.alive &&
        ((l.a===from && l.b===to) || (l.a===to && l.b===from)));
      const q = link ? link.quality : 0.1;
      let hopOk = false;
      for (let r = 0; r < MAX_RETRIES; r++) {
        if (rng.next() <= q) { hopOk = true; break; }
        retries++;
        txEvents.push({ from, to, time: tick++ });
      }
      if (!hopOk) { delivered = false; failHop = i; break; }
    }

    if (delivered) {
      return { txEvents, delivered: true, totalTx: txEvents.length, path, retries, failHop: -1, fallback: false };
    }
  }

  // All direct paths failed — scoped cluster fallback flooding
  const fallback = fallbackClusterFlood(net, src, dst, rng);
  return { ...fallback, fallback: true };
}

// BFS excluding certain intermediate nodes (for multi-path)
function bfsPathExcluding(nodes, links, src, dst, excluded) {
  if (src === dst) return [src];
  const visited = new Set([src]);
  const queue = [[src, [src]]];
  while (queue.length) {
    const [cur, path] = queue.shift();
    for (const l of links) {
      if (!l.alive) continue;
      let neighbor = -1;
      if (l.a === cur) neighbor = l.b;
      else if (l.b === cur) neighbor = l.a;
      else continue;
      if (visited.has(neighbor)) continue;
      if (nodes[neighbor].battery <= 0) continue;
      if (neighbor !== dst && excluded.has(neighbor)) continue;
      const newPath = [...path, neighbor];
      if (neighbor === dst) return newPath;
      visited.add(neighbor);
      queue.push([neighbor, newPath]);
    }
  }
  return null;
}

// Scoped flooding: src cluster + dst cluster + border neighbors
function fallbackClusterFlood(net, src, dst, rng) {
  const srcCluster = net.nodes[src].cluster;
  const dstCluster = net.nodes[dst].cluster;
  const floodNodes = new Set();

  // Include all nodes in src + dst clusters
  for (const n of net.nodes) {
    if (n.battery <= 0) continue;
    if (n.cluster === srcCluster || n.cluster === dstCluster) floodNodes.add(n.id);
    // Border nodes from any cluster + their neighbors
    if (n.border) {
      floodNodes.add(n.id);
      for (const nid of Object.keys(n.neighbors)) {
        if (net.nodes[+nid].battery > 0) floodNodes.add(+nid);
      }
    }
  }

  const txEvents = [];
  const seen = new Set([src]);
  const queue = [[src, 0]];
  let delivered = false, tick = 0;

  while (queue.length) {
    const [cur, hop] = queue.shift();
    if (hop >= 20) continue;
    const node = net.nodes[cur];

    for (const [nidStr, quality] of Object.entries(node.neighbors)) {
      const nid = +nidStr;
      if (!floodNodes.has(nid)) continue;
      const link = net.links.find(l => l.alive &&
        ((l.a===cur && l.b===nid) || (l.a===nid && l.b===cur)));
      if (!link) continue;

      txEvents.push({ from: cur, to: nid, time: tick++, hop });
      if (rng.next() > quality) continue;
      if (seen.has(nid)) continue;
      seen.add(nid);
      if (nid === dst) { delivered = true; continue; }
      if (net.nodes[nid].battery <= 0) continue;
      queue.push([nid, hop + 1]);
    }
  }

  return {
    txEvents, delivered, totalTx: txEvents.length,
    path: null, retries: 0, failHop: -1,
  };
}


// ---- EchoRoute: Zero-overhead directed routing via passive learning ----
// Learns routes by "hearing the echo" of network traffic. No flooding.
// 3 rules: Listen → Learn → Forward directly (or drop if no route known).

const _echoRouteTables = new Map(); // nodeId -> Map(destId -> {nextHop, hops, quality, tick})
let _echoBootstrappedNetId = null;  // cache: only bootstrap once per network

function echoRouteReset() {
  _echoRouteTables.clear();
  _echoBootstrappedNetId = null;
}

function _echoLearnRoute(nodeId, destId, nextHop, hops, quality, tick) {
  if (nodeId === destId) return;
  if (!_echoRouteTables.has(nodeId)) _echoRouteTables.set(nodeId, new Map());
  const table = _echoRouteTables.get(nodeId);
  const existing = table.get(destId);
  if (existing && existing.nextHop === nextHop) {
    existing.tick = tick;
    if (hops < existing.hops) existing.hops = hops;
    if (quality > existing.quality) existing.quality = quality;
    return;
  }
  if (!existing || quality > existing.quality || hops < existing.hops) {
    table.set(destId, { nextHop, hops, quality, tick });
  }
}

function _echoBootstrap(net) {
  // Cache: only bootstrap once per network (expensive for large networks)
  const netId = net.nodes.length + '_' + (net.nodes[0] ? net.nodes[0].x : 0);
  if (_echoBootstrappedNetId === netId && _echoRouteTables.size > 0) return;
  _echoRouteTables.clear();
  _echoBootstrappedNetId = netId;

  const N = net.nodes.length;
  const QUALITY_MIN = 0.05;
  const MAX_HOPS = N > 500 ? 6 : 10;
  const MAX_ROUTES = N > 500 ? 60 : 80;

  // Dijkstra bootstrap: find most RELIABLE paths (not shortest)
  // Edge weight = -log(quality), so Dijkstra minimizes = maximizes reliability
  for (const srcNode of net.nodes) {
    if (srcNode.battery <= 0) continue;

    // Mini-Dijkstra per source node
    const dist = new Map();    // nodeId -> best distance
    const firstHop = new Map(); // nodeId -> first hop from src
    const hops = new Map();     // nodeId -> hop count
    dist.set(srcNode.id, 0);
    hops.set(srcNode.id, 0);

    // Simple priority queue via sorted array (good enough for limited exploration)
    const open = [[0, srcNode.id]]; // [dist, nodeId]
    let count = 0;

    while (open.length && count < MAX_ROUTES) {
      // Pop minimum distance
      let minIdx = 0;
      for (let i = 1; i < open.length; i++) {
        if (open[i][0] < open[minIdx][0]) minIdx = i;
      }
      const [d, cur] = open.splice(minIdx, 1)[0];

      if (d > dist.get(cur)) continue;
      const curHops = hops.get(cur) || 0;
      if (curHops >= MAX_HOPS) continue;

      const curNode = net.nodes[cur];
      if (!curNode) continue;

      for (const [nbStr, q] of Object.entries(curNode.neighbors)) {
        const nb = +nbStr;
        if (q < QUALITY_MIN) continue;
        if (net.nodes[nb].battery <= 0) continue;

        const w = -Math.log(Math.max(q, 0.001));
        const newDist = d + w;

        if (newDist < (dist.get(nb) ?? Infinity)) {
          dist.set(nb, newDist);
          hops.set(nb, curHops + 1);
          const fh = cur === srcNode.id ? nb : (firstHop.get(cur) || nb);
          firstHop.set(nb, fh);
          open.push([newDist, nb]);

          // Reliability = exp(-dist) = product of link qualities along path
          const reliability = Math.exp(-newDist);
          _echoLearnRoute(srcNode.id, nb, fh, curHops + 1, reliability, 0);
          count++;
        }
      }
    }
  }
}

function _echoLearnFromPath(net, path, tick) {
  for (let idx = 0; idx < path.length; idx++) {
    const nid = path[idx];
    // Learn forward
    if (idx < path.length - 1) {
      const nh = path[idx + 1];
      const q = net.nodes[nid].neighbors[nh] || 0.3;
      for (let j = idx + 1; j < path.length; j++) {
        _echoLearnRoute(nid, path[j], nh, j - idx, q, tick);
      }
    }
    // Learn backward
    if (idx > 0) {
      const nh = path[idx - 1];
      const q = net.nodes[nid].neighbors[nh] || 0.3;
      for (let j = idx - 1; j >= 0; j--) {
        _echoLearnRoute(nid, path[j], nh, idx - j, q, tick);
      }
    }
  }
  // Neighbors overhearing
  const overheard = new Set(path);
  for (const nid of path) {
    const node = net.nodes[nid];
    if (!node) continue;
    for (const nbStr of Object.keys(node.neighbors)) {
      const nb = +nbStr;
      if (overheard.has(nb)) continue;
      overheard.add(nb);
      const idx = path.indexOf(nid);
      const q = node.neighbors[nb] || 0.3;
      for (let j = 0; j < path.length; j++) {
        if (path[j] === nb) continue;
        _echoLearnRoute(nb, path[j], nid, Math.abs(j - idx) + 1, q, 0);
      }
    }
  }
}

function simulateEchoRoute(net, src, dst, rng) {
  _echoBootstrap(net); // cached — only runs once per network

  const txEvents = [];
  let tick = 0;
  let current = src;
  const path = [src];
  const visited = new Set([src]);
  const triedNh = new Map(); // per node: set of tried next-hops
  const MAX_HOPS = 15;

  while (current !== dst && path.length <= MAX_HOPS) {
    // Find best route from current to dst
    const table = _echoRouteTables.get(current);
    const exclude = triedNh.get(current) || new Set();
    let bestRoute = null;

    if (table && table.has(dst)) {
      const r = table.get(dst);
      if (!exclude.has(r.nextHop) && !visited.has(r.nextHop)) {
        const node = net.nodes[current];
        if (node.neighbors[r.nextHop] !== undefined && net.nodes[r.nextHop].battery > 0) {
          bestRoute = r;
        }
      }
    }

    // Also check alternative routes: any neighbor that has dst in its table
    if (!bestRoute) {
      const node = net.nodes[current];
      const candidates = Object.entries(node.neighbors)
        .filter(([nid]) => !exclude.has(+nid) && !visited.has(+nid) && net.nodes[+nid].battery > 0)
        .sort((a, b) => b[1] - a[1]);
      for (const [nidStr, q] of candidates.slice(0, 3)) {
        const nid = +nidStr;
        const nhTable = _echoRouteTables.get(nid);
        if (nhTable && nhTable.has(dst)) {
          bestRoute = { nextHop: nid, hops: nhTable.get(dst).hops + 1, quality: q };
          break;
        }
      }
    }

    if (!bestRoute) break; // no route — drop packet

    const nh = bestRoute.nextHop;
    if (!triedNh.has(current)) triedNh.set(current, new Set());
    triedNh.get(current).add(nh);

    const linkQ = net.nodes[current].neighbors[nh] || 0.01;
    const maxRetries = linkQ > 0.5 ? 3 : (linkQ > 0.2 ? 5 : 8);
    let hopOk = false;

    for (let r = 0; r < maxRetries; r++) {
      txEvents.push({ from: current, to: nh, time: tick++, hop: path.length - 1 });
      if (rng.next() <= linkQ) { hopOk = true; break; }
    }

    if (!hopOk) {
      // Try alternative from same node (up to 2 more)
      continue;
    }

    visited.add(nh);
    path.push(nh);
    current = nh;
  }

  const delivered = current === dst;
  if (delivered) {
    _echoLearnFromPath(net, path, tick);
  }

  return {
    txEvents,
    delivered,
    totalTx: txEvents.length,
    path: delivered ? path : null,
    retries: txEvents.length - (delivered ? path.length - 1 : path.length),
    failHop: delivered ? -1 : path.length - 1,
    fallback: false,
    echoRoute: true,
  };
}


// ---- WalkFlood: Passive Learning + Walk + Mini-Flood ----
// Nodes that successfully participate in directed routing get marked
// with .isWalkFlood = true (purple ring) to visualize the migration sweep.

function _markWalkFloodNodes(net, path) {
  // Every node on a successful directed delivery path "upgrades" to WalkFlood
  if (!path) return;
  for (const nid of path) {
    if (net.nodes[nid]) net.nodes[nid].isWalkFlood = true;
  }
}

// Track how many messages have been routed (for learning progression)
let _wfMessageCount = 0;
let _wfLastNetId = null;

function walkFloodReset() {
  _wfMessageCount = 0;
  _wfLastNetId = null;
  _echoRouteTables.clear();
  _echoBootstrappedNetId = null;
}

function simulateWalkFlood(net, src, dst, rng) {
  // Check if network changed
  const netId = net.nodes.length + '_' + (net.nodes[0] ? net.nodes[0].x : 0);
  if (_wfLastNetId !== netId) {
    walkFloodReset();
    _wfLastNetId = netId;
    // Phase 0: Learn ONLY direct neighbors (from NodeInfo — free, instant)
    for (const node of net.nodes) {
      if (node.battery <= 0) continue;
      for (const [nidStr, q] of Object.entries(node.neighbors)) {
        if (q >= 0.05) _echoLearnRoute(node.id, +nidStr, +nidStr, 1, q, 0);
      }
    }
    _echoBootstrappedNetId = netId; // prevent full bootstrap from running
  }
  _wfMessageCount++;

  // After 5 messages: learn 2-hop routes (from overheard traffic)
  if (_wfMessageCount === 5) {
    for (const node of net.nodes) {
      if (node.battery <= 0) continue;
      for (const [nbStr, nbQ] of Object.entries(node.neighbors)) {
        const nb = +nbStr;
        if (nbQ < 0.05) continue;
        const nbNode = net.nodes[nb];
        if (!nbNode || nbNode.battery <= 0) continue;
        for (const [nb2Str, nb2Q] of Object.entries(nbNode.neighbors)) {
          const nb2 = +nb2Str;
          if (nb2 === node.id || nb2Q < 0.05) continue;
          _echoLearnRoute(node.id, nb2, nb, 2, Math.min(nbQ, nb2Q), 0);
        }
      }
    }
  }

  // After 10 messages: full Dijkstra bootstrap (network has been running long enough)
  if (_wfMessageCount === 10) {
    _echoBootstrappedNetId = null; // force re-bootstrap
    _echoBootstrap(net);
  }

  // Check: does source know a route to dst?
  const srcTable = _echoRouteTables.get(src);
  const hasRoute = srcTable && srcTable.has(dst);

  // If no route known: use managed flooding (identical to left panel)
  if (!hasRoute && _wfMessageCount <= 10) {
    const floodResult = simulateManagedFlood(net, src, dst, rng, 7);
    // Learn from flood delivery — use BFS path as proxy for actual delivery path
    const learnPath = bfsPath(net.nodes, net.links, src, dst);
    if (floodResult.delivered && learnPath) {
      _echoLearnFromPath(net, learnPath, _wfMessageCount);
      // DON'T mark as WalkFlood yet — this was a flood
    }
    // Return with path so the engine can display it
    return {
      ...floodResult,
      path: floodResult.delivered ? learnPath : null,
      walkFlood: true,
      phase: 'flood (learning)',
    };
  }

  const txEvents = [];
  let tick = 0;

  // Directed routing with Walk+MiniFlood fallback
  let current = src;
  const path = [src];
  const visited = new Set([src]);
  const triedNh = new Map();
  const MAX_HOPS = 15;
  let directed = true;

  while (current !== dst && path.length <= MAX_HOPS) {
    const table = _echoRouteTables.get(current);
    const exclude = triedNh.get(current) || new Set();
    let bestRoute = null;

    if (table && table.has(dst)) {
      const r = table.get(dst);
      if (!exclude.has(r.nextHop) && !visited.has(r.nextHop)) {
        const node = net.nodes[current];
        if (node.neighbors[r.nextHop] !== undefined && net.nodes[r.nextHop].battery > 0) {
          bestRoute = r;
        }
      }
    }
    if (!bestRoute) {
      const node = net.nodes[current];
      const cands = Object.entries(node.neighbors)
        .filter(([n]) => !exclude.has(+n) && !visited.has(+n) && net.nodes[+n].battery > 0)
        .sort((a, b) => b[1] - a[1]);
      for (const [nStr, q] of cands.slice(0, 3)) {
        const nhT = _echoRouteTables.get(+nStr);
        if (nhT && nhT.has(dst)) {
          bestRoute = { nextHop: +nStr, hops: nhT.get(dst).hops + 1, quality: q };
          break;
        }
      }
    }

    if (!bestRoute) { directed = false; break; }

    const nh = bestRoute.nextHop;
    if (!triedNh.has(current)) triedNh.set(current, new Set());
    triedNh.get(current).add(nh);
    const linkQ = net.nodes[current].neighbors[nh] || 0.01;
    const maxR = linkQ > 0.5 ? 3 : (linkQ > 0.2 ? 5 : 8);
    let hopOk = false;
    for (let r = 0; r < maxR; r++) {
      txEvents.push({ from: current, to: nh, time: tick++, hop: path.length - 1 });
      if (rng.next() <= linkQ) { hopOk = true; break; }
    }
    if (!hopOk) { directed = false; break; }
    visited.add(nh);
    path.push(nh);
    current = nh;
  }

  if (current === dst) {
    _echoLearnFromPath(net, path, tick);
    _markWalkFloodNodes(net, path);
    return { txEvents, delivered: true, totalTx: txEvents.length, path,
             retries: txEvents.length - path.length + 1, failHop: -1,
             fallback: false, walkFlood: true, phase: 'directed' };
  }

  // Phase 2: Walk toward destination (5 steps)
  const walkVisited = new Set(visited);
  for (let step = 0; step < 5 && current !== dst; step++) {
    const node = net.nodes[current];
    const cands = [];
    for (const [nbStr, q] of Object.entries(node.neighbors)) {
      const nb = +nbStr;
      if (walkVisited.has(nb) || q < 0.03 || net.nodes[nb].battery <= 0) continue;
      const routes = _echoRouteTables.get(nb);
      const hasRoute = routes && routes.has(dst) ? 1 : 0;
      const minH = hasRoute ? routes.get(dst).hops : 99;
      const deg = Object.keys(net.nodes[nb].neighbors).length;
      cands.push({ id: nb, q, score: hasRoute * 1000 - minH + q * 10 + deg * 0.1 });
    }
    if (!cands.length) break;
    cands.sort((a, b) => b.score - a.score);
    const best = cands[0];
    let ok = false;
    for (let r = 0; r < 8; r++) {
      txEvents.push({ from: current, to: best.id, time: tick++, hop: path.length - 1 });
      if (rng.next() <= best.q) { ok = true; break; }
    }
    if (!ok) break;
    walkVisited.add(best.id);
    path.push(best.id);
    current = best.id;
    if (current === dst) {
      _echoLearnFromPath(net, path, tick);
      _markWalkFloodNodes(net, path);
      return { txEvents, delivered: true, totalTx: txEvents.length, path,
               retries: 0, failHop: -1, fallback: false, walkFlood: true, phase: 'walk' };
    }
    // Try directed from new position
    const tbl = _echoRouteTables.get(current);
    if (tbl && tbl.has(dst)) {
      const r = tbl.get(dst);
      const lq = net.nodes[current].neighbors[r.nextHop];
      if (lq !== undefined && !walkVisited.has(r.nextHop)) {
        let hOk = false;
        for (let rt = 0; rt < 8; rt++) {
          txEvents.push({ from: current, to: r.nextHop, time: tick++, hop: path.length - 1 });
          if (rng.next() <= lq) { hOk = true; break; }
        }
        if (hOk) {
          walkVisited.add(r.nextHop);
          path.push(r.nextHop);
          current = r.nextHop;
          if (current === dst) {
            _echoLearnFromPath(net, path, tick);
            return { txEvents, delivered: true, totalTx: txEvents.length, path,
                     retries: 0, failHop: -1, fallback: false, walkFlood: true, phase: 'walk+direct' };
          _markWalkFloodNodes(net, sub_path);
          }
        }
      }
    }
  }

  // Phase 3: Mini-flood (2 best neighbors, 4 hops)
  const floodSeen = new Set([current]);
  const floodQueue = [[current, 0, [...path]]];
  let deliveryPath = null;
  while (floodQueue.length) {
    const [cur, hop, fp] = floodQueue.shift();
    if (hop >= 4) continue;
    const cNode = net.nodes[cur];
    const sorted = Object.entries(cNode.neighbors).sort((a, b) => b[1] - a[1]);
    let relayed = 0;
    for (const [nbStr, q] of sorted) {
      const nb = +nbStr;
      if (floodSeen.has(nb)) continue;
      if (relayed >= 2 && nb !== dst) continue;
      txEvents.push({ from: cur, to: nb, time: tick++, hop: path.length - 1 + hop });
      if (rng.next() > q) continue;
      floodSeen.add(nb);
      relayed++;
      const np = [...fp, nb];
      if (nb === dst) { if (!deliveryPath || np.length < deliveryPath.length) deliveryPath = np; continue; }
      floodQueue.push([nb, hop + 1, np]);
    }
  }

  if (deliveryPath) {
    _echoLearnFromPath(net, deliveryPath, tick);
    _markWalkFloodNodes(net, deliveryPath);
    return { txEvents, delivered: true, totalTx: txEvents.length, path: deliveryPath,
             retries: 0, failHop: -1, fallback: true, walkFlood: true, phase: 'mini-flood' };
  }

  return { txEvents, delivered: false, totalTx: txEvents.length, path: null,
           retries: 0, failHop: path.length - 1, fallback: false, walkFlood: true, phase: 'dropped' };
}


// ---- WalkFlood Broadcast: MPR-based efficient broadcast ----
// Computes Multi-Point Relay set for the source, then floods via MPR nodes only.
// Non-MPR nodes receive but do NOT rebroadcast — dramatically fewer TX than managed flooding.

function _computeMPR(net, nodeId) {
  // Greedy MPR selection: pick 1-hop neighbors that cover the most uncovered 2-hop neighbors
  const node = net.nodes[nodeId];
  if (!node || node.battery <= 0) return new Set();

  const oneHop = new Set();
  for (const [nidStr, q] of Object.entries(node.neighbors)) {
    const nid = +nidStr;
    if (q >= 0.05 && net.nodes[nid].battery > 0) oneHop.add(nid);
  }

  // Collect all 2-hop neighbors (reachable through 1-hop, but not 1-hop themselves and not self)
  const twoHopMap = new Map(); // 2-hop node -> set of 1-hop nodes that cover it
  for (const nb of oneHop) {
    const nbNode = net.nodes[nb];
    if (!nbNode) continue;
    for (const [nb2Str, q2] of Object.entries(nbNode.neighbors)) {
      const nb2 = +nb2Str;
      if (nb2 === nodeId || oneHop.has(nb2) || q2 < 0.05 || net.nodes[nb2].battery <= 0) continue;
      if (!twoHopMap.has(nb2)) twoHopMap.set(nb2, new Set());
      twoHopMap.get(nb2).add(nb);
    }
  }

  const mprSet = new Set();
  const uncovered = new Set(twoHopMap.keys());

  // Step 1: Any 2-hop node reachable through only ONE 1-hop neighbor -> that neighbor is mandatory MPR
  for (const [twoHop, coverSet] of twoHopMap) {
    if (coverSet.size === 1) {
      const mandatory = coverSet.values().next().value;
      mprSet.add(mandatory);
    }
  }

  // Remove covered 2-hop nodes
  for (const mpr of mprSet) {
    const mprNode = net.nodes[mpr];
    if (!mprNode) continue;
    for (const nb2Str of Object.keys(mprNode.neighbors)) {
      uncovered.delete(+nb2Str);
    }
  }

  // Step 2: Greedy — pick 1-hop neighbor covering the most remaining uncovered 2-hop nodes
  while (uncovered.size > 0) {
    let bestNb = -1, bestCount = 0;
    for (const nb of oneHop) {
      if (mprSet.has(nb)) continue;
      const nbNode = net.nodes[nb];
      if (!nbNode) continue;
      let count = 0;
      for (const nb2Str of Object.keys(nbNode.neighbors)) {
        if (uncovered.has(+nb2Str)) count++;
      }
      if (count > bestCount) { bestCount = count; bestNb = nb; }
    }
    if (bestNb < 0 || bestCount === 0) break;
    mprSet.add(bestNb);
    const bestNode = net.nodes[bestNb];
    for (const nb2Str of Object.keys(bestNode.neighbors)) {
      uncovered.delete(+nb2Str);
    }
  }

  return mprSet;
}

function simulateWalkFloodBroadcast(net, src, rng, hopLimit = 7) {
  // MPR-based broadcast: only MPR-elected nodes rebroadcast
  const txEvents = [];
  const seen = new Set([src]);
  const rebroadcast = new Set([src]);
  const mprRelayNodes = new Set(); // nodes that actually relayed (for visualization)
  const queue = [[src, 0]]; // [nodeId, hop]
  let tick = 0;
  const nodesReached = new Set([src]);
  const totalAlive = net.nodes.filter(n => n.battery > 0).length;

  // Pre-compute MPR sets for all nodes (cached per broadcast)
  const mprSets = new Map();
  for (const n of net.nodes) {
    if (n.battery <= 0) continue;
    mprSets.set(n.id, _computeMPR(net, n.id));
  }

  while (queue.length) {
    const [cur, hop] = queue.shift();
    if (hop >= hopLimit) continue;

    const node = net.nodes[cur];

    // Only the source and MPR-designated nodes rebroadcast
    if (cur !== src) {
      // Check: was this node designated as MPR by whoever sent to it?
      // In MPR flooding, a node rebroadcasts only if its sender selected it as MPR
      const senderMPR = mprSets.get(cur);
      // The node rebroadcasts if it was selected as MPR by ANY node that already rebroadcasted
      let isMPR = false;
      for (const prevRelay of rebroadcast) {
        const prevMPRSet = mprSets.get(prevRelay);
        if (prevMPRSet && prevMPRSet.has(cur)) { isMPR = true; break; }
      }
      if (!isMPR) continue; // Not an MPR — receive only, don't rebroadcast
    }

    rebroadcast.add(cur);
    if (cur !== src) mprRelayNodes.add(cur);

    for (const [nidStr, quality] of Object.entries(node.neighbors)) {
      const nid = +nidStr;
      const link = net.links.find(l => l.alive &&
        ((l.a===cur && l.b===nid) || (l.a===nid && l.b===cur)));
      if (!link) continue;

      txEvents.push({ from: cur, to: nid, time: tick++, hop });

      if (rng.next() > quality) continue;
      if (seen.has(nid)) continue;
      seen.add(nid);
      nodesReached.add(nid);
      if (net.nodes[nid].battery <= 0) continue;
      queue.push([nid, hop + 1]);
    }
  }

  // Mark MPR relay nodes on the network for visualization (purple ring)
  for (const nid of mprRelayNodes) {
    if (net.nodes[nid]) net.nodes[nid].isMprRelay = true;
  }

  const hopGroups = {};
  let maxHop = 0;
  for (const ev of txEvents) {
    if (!hopGroups[ev.hop]) hopGroups[ev.hop] = [];
    hopGroups[ev.hop].push(ev);
    if (ev.hop > maxHop) maxHop = ev.hop;
  }

  return {
    txEvents, totalTx: txEvents.length, hopGroups, maxHop,
    nodesReached, totalAlive, reachPct: (nodesReached.size / totalAlive * 100),
    mprRelayNodes, rebroadcasters: rebroadcast.size,
  };
}
