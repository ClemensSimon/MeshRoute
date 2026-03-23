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
