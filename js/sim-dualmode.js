// MeshRoute Simulator - Dual-Mode (Mixed S5/Legacy) Routing

// Dual-mode routing: S5 nodes route directly between each other,
// Legacy nodes flood. When an S5 node needs to reach a legacy node
// (or vice versa), it falls back to flooding through that segment.
function simulateDualMode(net, src, dst, rng, hopLimit = 7) {
  const srcNode = net.nodes[src], dstNode = net.nodes[dst];
  const bothS5 = srcNode.isS5 && dstNode.isS5;

  // Check if there's an all-S5 path (SRC must be S5; intermediates must be S5; DST can be anything)
  if (srcNode.isS5) {
    const s5Path = bfsPathS5Only(net.nodes, net.links, src, dst);
    if (s5Path) {
      // Pure S5 directed routing — like System 5
      const txEvents = [];
      let tick = 0, delivered = true, retries = 0, failHop = -1;
      for (let i = 0; i < s5Path.length - 1; i++) {
        const from = s5Path[i], to = s5Path[i+1];
        txEvents.push({ from, to, time: tick++, hop: i, mode: 's5' });
        const link = net.links.find(l => l.alive &&
          ((l.a===from && l.b===to) || (l.a===to && l.b===from)));
        const q = link ? link.quality : 0.1;
        if (rng.next() > q) {
          retries++;
          txEvents.push({ from, to, time: tick++, hop: i, mode: 's5' });
          if (rng.next() > q) { delivered = false; failHop = i; break; }
        }
      }
      return {
        txEvents, delivered, totalTx: txEvents.length,
        path: s5Path, retries, failHop, mode: 'direct',
        s5Hops: s5Path.length - 1,
      };
    }
  }

  // Fallback: mixed flooding — same suppression as legacy (40%) for compatibility,
  // but S5 nodes that see an S5 neighbor on the BFS-path send ONLY to that neighbor
  // instead of broadcasting to ALL. This is the real TX saving.
  const txEvents = [];
  const seen = new Set([src]);
  const rebroadcast = new Set([src]);
  const queue = [[src, 0]];
  let delivered = false, tick = 0, suppressed = 0;
  const nodesReached = new Set([src]);

  // Pre-compute BFS next-hop hints for S5 nodes (which S5 neighbor to send to)
  const s5NextHop = {};
  const fullPath = bfsPath(net.nodes, net.links, src, dst, true);
  if (fullPath) {
    for (let i = 0; i < fullPath.length - 1; i++) {
      s5NextHop[fullPath[i]] = fullPath[i + 1];
    }
  }

  while (queue.length) {
    const [cur, hop] = queue.shift();
    const node = net.nodes[cur];

    // Hop limit: S5 nodes ignore it (directed routing = 1 TX/hop, no broadcast storm)
    // Legacy nodes respect it (they flood, so hop limit prevents storm)
    if (!node.isS5) {
      if (hop >= hopLimit) continue;
    }
    if (hop >= S5_MAX_HOPS) continue; // safety cap for all nodes

    if (cur !== src) {
      // Suppression: only for legacy nodes. S5 nodes don't suppress — they route directed.
      if (!node.isS5) {
        const neighborRebroadcasted = Object.keys(node.neighbors).some(
          nid => rebroadcast.has(+nid) && +nid !== src && seen.has(+nid)
        );
        if (neighborRebroadcasted && rng.next() < 0.4) {
          suppressed++;
          continue;
        }
      }
    }

    rebroadcast.add(cur);

    // S5 node: directed routing via routing table (1 TX)
    if (node.isS5 && s5NextHop[cur] != null) {
      const nid = s5NextHop[cur];
      if (net.nodes[nid].battery > 0) {
        const link = net.links.find(l => l.alive &&
          ((l.a===cur && l.b===nid) || (l.a===nid && l.b===cur)));
        if (link) {
          txEvents.push({ from: cur, to: nid, time: tick++, hop, mode: 's5' });
          if (rng.next() <= link.quality && !seen.has(nid)) {
            seen.add(nid); nodesReached.add(nid);
            if (nid === dst) { delivered = true; }
            else if (net.nodes[nid].battery > 0) queue.push([nid, hop + 1]);
          }
        }
      }
    } else {
      // Legacy node (or S5 without routing info): flood to ALL neighbors
      // But prefer S5 neighbors first (they can route further without flooding)
      const neighborIds = Object.keys(node.neighbors).map(Number);
      neighborIds.sort((a, b) => {
        // S5 neighbors first, then by link quality
        const aS5 = net.nodes[a].isS5 ? 1 : 0;
        const bS5 = net.nodes[b].isS5 ? 1 : 0;
        if (aS5 !== bS5) return bS5 - aS5;
        return (node.neighbors[b] || 0) - (node.neighbors[a] || 0);
      });

      for (const nid of neighborIds) {
        const quality = node.neighbors[nid];
        const link = net.links.find(l => l.alive &&
          ((l.a===cur && l.b===nid) || (l.a===nid && l.b===cur)));
        if (!link) continue;

        const mode = (node.isS5 && net.nodes[nid].isS5) ? 's5' : 'flood';
        txEvents.push({ from: cur, to: nid, time: tick++, hop, mode });
        if (rng.next() > quality) continue;
        if (seen.has(nid)) continue;
        seen.add(nid); nodesReached.add(nid);
        if (nid === dst) { delivered = true; continue; }
        if (net.nodes[nid].battery <= 0) continue;
        queue.push([nid, hop + 1]);
      }
    }
  }

  return {
    txEvents, delivered, totalTx: txEvents.length,
    suppressed, nodesReached: nodesReached.size,
    rebroadcasters: rebroadcast.size,
    mode: 'mixed', hopLimit,
  };
}

// BFS that only routes through S5 nodes (and visible links)
function bfsPathS5Only(nodes, links, src, dst) {
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
      // Must pass through S5 nodes (except destination can be anything)
      if (neighbor !== dst && !nodes[neighbor].isS5) continue;
      const newPath = [...path, neighbor];
      if (neighbor === dst) return newPath;
      visited.add(neighbor);
      queue.push([neighbor, newPath]);
    }
  }
  return null;
}

