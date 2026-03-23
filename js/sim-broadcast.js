// MeshRoute Simulator - Broadcast Routing Algorithms

// ---- Broadcast Simulation Functions ----

function simulateManagedBroadcast(net, src, rng, hopLimit = 7) {
  // Managed flooding broadcast: measure how many nodes receive the message
  const txEvents = [];
  const seen = new Set([src]);
  const rebroadcast = new Set([src]);
  const queue = [[src, 0]];
  let tick = 0;
  const nodesReached = new Set([src]);
  const totalAlive = net.nodes.filter(n => n.battery > 0).length;

  while (queue.length) {
    const [cur, hop] = queue.shift();
    if (hop >= hopLimit) continue;
    const node = net.nodes[cur];

    if (cur !== src) {
      const neighborRebroadcasted = Object.keys(node.neighbors).some(
        nid => rebroadcast.has(+nid) && +nid !== src && seen.has(+nid)
      );
      if (neighborRebroadcasted && rng.next() < 0.4) continue;
    }
    rebroadcast.add(cur);

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
  };
}

function electDistributors(net) {
  // Elect one distributor per cluster: valley node with high local reach, low leakage
  const clusters = {};
  for (const n of net.nodes) {
    if (n.battery <= 0) continue;
    if (!clusters[n.cluster]) clusters[n.cluster] = [];
    clusters[n.cluster].push(n.id);
  }

  const distributors = {};
  for (const [cid, members] of Object.entries(clusters)) {
    const memberSet = new Set(members);
    let bestNode = members[0], bestScore = -1;

    const elevations = members.map(nid => net.nodes[nid].elevation || 0);
    const minElev = Math.min(...elevations);
    const maxElev = Math.max(...elevations);
    const elevRange = Math.max(maxElev - minElev, 1);

    for (const nid of members) {
      const node = net.nodes[nid];
      const nbKeys = Object.keys(node.neighbors);
      if (nbKeys.length === 0) continue;

      const nbIn = nbKeys.filter(nb => memberSet.has(+nb)).length;
      const coverage = nbIn / members.length;
      const containment = 1 - ((nbKeys.length - nbIn) / nbKeys.length);

      const elev = node.elevation || 0;
      const elevNorm = (elev - minElev) / elevRange;
      const elevBonus = 1.0 - 0.8 * elevNorm;

      const tier = node.tier || 'valley';
      const tierBonus = tier === 'mountain' ? 0.1 : tier === 'hill' ? 0.5 : 1.0;

      const score = coverage * (0.3 * containment + 0.4 * elevBonus + 0.3 * tierBonus);
      if (score > bestScore) { bestScore = score; bestNode = nid; }
    }
    distributors[cid] = bestNode;
  }
  return distributors;
}

function simulateClusterDistributorBroadcast(net, src, rng) {
  // Wave propagation: unicast to cluster distributors, local mini-flood per cluster
  const txEvents = [];
  const nodesReached = new Set([src]);
  const totalAlive = net.nodes.filter(n => n.battery > 0).length;
  let tick = 0;
  let hopLevel = 0;

  const distributors = electDistributors(net);

  // Build cluster adjacency
  const clusters = {};
  for (const n of net.nodes) {
    if (n.battery <= 0) continue;
    if (!clusters[n.cluster]) clusters[n.cluster] = [];
    clusters[n.cluster].push(n.id);
  }

  const clusterAdj = {};
  const borderBridges = {};
  for (const [cid, members] of Object.entries(clusters)) {
    clusterAdj[cid] = new Set();
    for (const nid of members) {
      if (!net.nodes[nid].border) continue;
      for (const nbStr of Object.keys(net.nodes[nid].neighbors)) {
        const nb = +nbStr;
        const nbCluster = String(net.nodes[nb].cluster);
        if (nbCluster !== cid && net.nodes[nb].battery > 0) {
          clusterAdj[cid].add(nbCluster);
          const key = cid + '->' + nbCluster;
          if (!borderBridges[key]) borderBridges[key] = [];
          if (!borderBridges[key].includes(nid)) borderBridges[key].push(nid);
        }
      }
    }
  }

  // BFS over clusters (wave propagation)
  const srcCluster = String(net.nodes[src].cluster);
  const floodedClusters = new Set();
  const clusterQueue = [srcCluster];

  while (clusterQueue.length) {
    const cid = clusterQueue.shift();
    if (floodedClusters.has(cid)) continue;
    floodedClusters.add(cid);

    const members = clusters[cid];
    if (!members) continue;
    const distId = distributors[cid];
    if (distId === undefined) continue;

    // Ensure distributor has the message
    if (!nodesReached.has(distId)) {
      if (cid === srcCluster && distId !== src) {
        // Unicast from src to distributor within own cluster
        const path = bfsPath(net.nodes, net.links, src, distId);
        if (path) {
          for (let i = 0; i < path.length - 1; i++) {
            txEvents.push({ from: path[i], to: path[i+1], time: tick++, hop: hopLevel, mode: 'unicast' });
            nodesReached.add(path[i+1]);
          }
        }
      } else if (cid !== srcCluster) {
        // Find a reached border bridge to relay
        let bridged = false;
        for (const prevCid of floodedClusters) {
          const key = prevCid + '->' + cid;
          const bridges = borderBridges[key] || [];
          const reached = bridges.filter(b => nodesReached.has(b));
          for (const bridgeId of reached.slice(0, 3)) {
            txEvents.push({ from: bridgeId, to: distId, time: tick++, hop: hopLevel, mode: 'bridge' });
            // Try to reach distributor
            const path = bfsPath(net.nodes, net.links, bridgeId, distId);
            if (path) {
              for (let i = 0; i < path.length - 1; i++) {
                txEvents.push({ from: path[i], to: path[i+1], time: tick++, hop: hopLevel, mode: 'unicast' });
                nodesReached.add(path[i+1]);
              }
              bridged = true;
              break;
            }
          }
          if (bridged) break;
        }
        if (!bridged) continue;
      }
    }
    nodesReached.add(distId);

    // Mini-flood within cluster from distributor
    const memberSet = new Set(members);
    const seen = new Set([distId]);
    const floodQ = [distId];
    hopLevel++;

    while (floodQ.length) {
      const cur = floodQ.shift();
      const curNode = net.nodes[cur];
      const tier = curNode.tier || 'valley';
      if (tier === 'mountain' && cur !== distId) continue; // don't relay from mountain

      for (const [nidStr, quality] of Object.entries(curNode.neighbors)) {
        const nid = +nidStr;
        if (seen.has(nid)) continue;
        seen.add(nid);
        txEvents.push({ from: cur, to: nid, time: tick++, hop: hopLevel, mode: 'flood' });
        if (rng.next() > quality) continue;
        nodesReached.add(nid);
        if (memberSet.has(nid) && net.nodes[nid].battery > 0) {
          floodQ.push(nid);
        }
      }
    }

    // Queue adjacent clusters
    for (const adjCid of (clusterAdj[cid] || [])) {
      if (!floodedClusters.has(adjCid)) clusterQueue.push(adjCid);
    }
    hopLevel++;
  }

  // Group by hop
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
    distributors,
  };
}

// Dual-mode routing: S5 nodes route directly between each other,
