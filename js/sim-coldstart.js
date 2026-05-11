// MeshRoute Simulator - Cold Start Self-Organization Mode
// Shows how System 5 bootstraps from zero knowledge to directed routing.
// Left panel: Managed Flooding (works immediately, no setup).
// Right panel: System 5 phases — network knowledge builds step by step.

const COLDSTART_PHASES = [
  { id: 'poweron', title: 'Nodes Power On',
    desc: 'Nodes boot up. Each knows only its own ID and GPS position. No topology knowledge — every node is isolated.' },
  { id: 'hello', title: 'Neighbor Discovery (HELLO)',
    desc: 'Each node broadcasts a HELLO beacon at full power. Nodes within radio range hear it and record the sender with RSSI signal strength.' },
  { id: 'links', title: 'Link Establishment',
    desc: 'Bidirectional links form between all node pairs that heard each other. Link quality = signal strength. This is the raw mesh topology.' },
  { id: 'cluster', title: 'Geo-Cluster Formation',
    desc: 'Each node computes its geographic cluster from GPS position. Nodes sharing a cluster will exchange full topology. Cluster regions appear.' },
  { id: 'border', title: 'Border Node Election',
    desc: 'Nodes with neighbors in OTHER clusters become border nodes — the gateways between clusters. Shown with white rings.' },
  { id: 'ogm', title: 'Route Table Building (OGMs)',
    desc: 'Originator Messages (OGMs) propagate through the mesh. Each node learns the best next-hop to every destination. Watch routes converge.' },
  { id: 'ready', title: 'Self-Organization Complete',
    desc: 'Every node now has a routing table. System 5 can route messages along directed paths — 1 TX per hop, no flooding. Select SRC/DST and press Step to compare.' },
];

let coldstartState = null;

function isColdstartScenario(scenarioKey) {
  const cfg = SCENARIOS[scenarioKey];
  return cfg && cfg.coldstart === true;
}

function initColdstart() {
  coldstartState = {
    phase: -1,
    bootstrapDone: false,
  };
  return coldstartState;
}

function coldstartPhaseCount() {
  return COLDSTART_PHASES.length;
}

// Build a "bare" network — nodes only, no links, no clusters, no borders
function buildBareNetwork(scenarioKey, rng) {
  const net = buildNetwork(scenarioKey, rng);
  // Store full data but mark everything as hidden initially
  for (const l of net.links) {
    l._realAlive = l.alive;
    l.alive = false; // hide all links
  }
  for (const n of net.nodes) {
    n._realNeighbors = { ...n.neighbors };
    n.neighbors = {};           // no neighbors known yet
    n._realCluster = n.cluster;
    n.cluster = 0;              // all same cluster (no visual separation)
    n._realBorder = n.border;
    n.border = false;           // no borders yet
  }
  // Clear cluster bounds (no clusters visible)
  net._realClusterBounds = net.clusterBounds;
  net.clusterBounds = null;
  net._realBridgeLinks = net.bridgeLinks;
  net.bridgeLinks = new Set();
  return net;
}

// Progressively reveal the network — returns animation events
function advanceColdstartPhase(net, renderer) {
  if (!coldstartState) return null;
  coldstartState.phase++;
  const p = coldstartState.phase;
  if (p >= COLDSTART_PHASES.length) return null;

  const phaseInfo = COLDSTART_PHASES[p];
  const events = []; // { from, to, hop, color } for packet animation

  switch (phaseInfo.id) {
    case 'poweron':
      // Nodes already visible (renderer draws them). Just a conceptual step.
      break;

    case 'hello': {
      // Show hello beacons: generate events from each node to its real neighbors
      for (const n of net.nodes) {
        if (n.battery <= 0) continue;
        for (const nidStr of Object.keys(n._realNeighbors)) {
          const nid = +nidStr;
          if (net.nodes[nid].battery <= 0) continue;
          events.push({ from: n.id, to: nid, hop: 0, color: '#fbbf24' });
        }
      }
      break;
    }

    case 'links':
      // Reveal all links and neighbor tables
      for (const l of net.links) {
        l.alive = l._realAlive;
      }
      for (const n of net.nodes) {
        n.neighbors = { ...n._realNeighbors };
      }
      break;

    case 'cluster':
      // Reveal clusters — reassign cluster IDs and show cluster bounds
      for (const n of net.nodes) {
        n.cluster = n._realCluster;
      }
      net.clusterBounds = net._realClusterBounds;
      break;

    case 'border':
      // Reveal border nodes and bridge links
      for (const n of net.nodes) {
        n.border = n._realBorder;
      }
      net.bridgeLinks = net._realBridgeLinks;
      break;

    case 'ogm': {
      // OGMs propagate along all links — each node sends to all neighbors
      // This represents route-table building via distance-vector exchange
      // Show as a BFS wave from a few seed nodes to visualize convergence
      const aliveNodes = net.nodes.filter(n => n.battery > 0);
      if (aliveNodes.length === 0) break;

      // Pick seed nodes: one per cluster (or first few if no clusters)
      const seeds = new Set();
      const seenClusters = new Set();
      for (const n of aliveNodes) {
        if (!seenClusters.has(n.cluster)) {
          seenClusters.add(n.cluster);
          seeds.add(n.id);
        }
      }

      const visited = new Set(seeds);
      let queue = [...seeds].map(id => [id, 0]);

      while (queue.length > 0) {
        const nextQueue = [];
        for (const [cur, hop] of queue) {
          const node = net.nodes[cur];
          for (const nidStr of Object.keys(node.neighbors)) {
            const nid = +nidStr;
            if (visited.has(nid)) continue;
            if (net.nodes[nid].battery <= 0) continue;
            visited.add(nid);
            nextQueue.push([nid, hop + 1]);
            events.push({ from: cur, to: nid, hop, color: '#a78bfa' });
          }
        }
        queue = nextQueue;
      }
      break;
    }

    case 'ready':
      coldstartState.bootstrapDone = true;
      break;
  }

  return { phaseInfo, phaseIndex: p, events };
}

function isColdstartBootstrapDone() {
  return coldstartState && coldstartState.bootstrapDone;
}

function getColdstartPhaseIndex() {
  return coldstartState ? coldstartState.phase : -1;
}
