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

function echoRouteReset() {
  _echoRouteTables.clear();
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
  // Phase 1: direct neighbors (from NodeInfo — free)
  for (const node of net.nodes) {
    if (node.battery <= 0) continue;
    for (const [nidStr, q] of Object.entries(node.neighbors)) {
      const nid = +nidStr;
      if (q >= 0.01) _echoLearnRoute(node.id, nid, nid, 1, q, 0);
    }
  }
  // Phase 2: 2-hop (from overhearing neighbors' forwarded traffic)
  for (const node of net.nodes) {
    if (node.battery <= 0) continue;
    for (const [nbStr, nbQ] of Object.entries(node.neighbors)) {
      const nb = +nbStr;
      if (nbQ < 0.01) continue;
      const nbNode = net.nodes[nb];
      if (!nbNode || nbNode.battery <= 0) continue;
      for (const [nb2Str, nb2Q] of Object.entries(nbNode.neighbors)) {
        const nb2 = +nb2Str;
        if (nb2 === node.id || nb2Q < 0.01) continue;
        _echoLearnRoute(node.id, nb2, nb, 2, Math.min(nbQ, nb2Q), 0);
      }
    }
  }
  // Phase 3: 3-4 hop via quality-BFS
  for (const srcNode of net.nodes) {
    if (srcNode.battery <= 0) continue;
    const visited = new Set([srcNode.id]);
    const queue = [[srcNode.id, srcNode.id, 1, 1.0]]; // [cur, firstHop, hops, minQ]
    let count = 0;
    while (queue.length && count < 60) {
      const [cur, firstHop, hops, pathQ] = queue.shift();
      if (hops > 4) continue;
      const curNode = net.nodes[cur];
      if (!curNode) continue;
      const sorted = Object.entries(curNode.neighbors).sort((a,b) => b[1] - a[1]);
      for (const [nbStr, q] of sorted) {
        const nb = +nbStr;
        if (visited.has(nb) || q < 0.01) continue;
        visited.add(nb);
        const newQ = Math.min(pathQ, q);
        const fh = hops > 1 ? firstHop : nb;
        _echoLearnRoute(srcNode.id, nb, fh, hops, newQ, 0);
        count++;
        if (hops < 4) queue.push([nb, fh, hops + 1, newQ]);
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
  echoRouteReset();
  _echoBootstrap(net);

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
function simulateWalkFlood(net, src, dst, rng) {
  echoRouteReset();
  _echoBootstrap(net);

  const txEvents = [];
  let tick = 0;

  // Phase 1: Try directed (same as EchoRoute)
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
    return { txEvents, delivered: true, totalTx: txEvents.length, path: deliveryPath,
             retries: 0, failHop: -1, fallback: true, walkFlood: true, phase: 'mini-flood' };
  }

  return { txEvents, delivered: false, totalTx: txEvents.length, path: null,
           retries: 0, failHop: path.length - 1, fallback: false, walkFlood: true, phase: 'dropped' };
}
