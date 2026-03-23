// MeshRoute Simulator - Network Builder

// ---- Network Builder ----
function buildNetwork(scenarioKey, rng) {
  const cfg = SCENARIOS[scenarioKey];
  const nodes = [];
  const links = [];

  // Place nodes
  if (cfg.placement === 'linear') {
    for (let i = 0; i < cfg.nodes; i++) {
      const t = i / Math.max(cfg.nodes - 1, 1);
      nodes.push({
        id: i, x: t * cfg.area,
        y: cfg.area / 2 + rng.gauss(0, cfg.area * 0.05),
        cluster: -1, border: false, battery: rng.uniform(50, 100),
        mobile: false, speed: 0, heading: 0, isS5: false, neighbors: {},
      });
    }
  } else if (cfg.placement === 'clustered') {
    const nClusters = Math.max(3, Math.floor(cfg.nodes / 15));
    const centers = [];
    for (let c = 0; c < nClusters; c++)
      centers.push([rng.uniform(cfg.area*0.1, cfg.area*0.9), rng.uniform(cfg.area*0.1, cfg.area*0.9)]);
    for (let i = 0; i < cfg.nodes; i++) {
      const [cx, cy] = rng.choice(centers);
      const spread = cfg.area * 0.08;
      nodes.push({
        id: i, x: Math.max(0, Math.min(cfg.area, cx + rng.gauss(0, spread))),
        y: Math.max(0, Math.min(cfg.area, cy + rng.gauss(0, spread))),
        cluster: -1, border: false, battery: rng.uniform(50, 100),
        mobile: false, speed: 0, heading: 0, isS5: false, neighbors: {},
      });
    }
  } else if (cfg.placement === 'bay_area') {
    // 3-tier Bay Area topology: mountain (3%), hill (15%), valley (82%)
    const nMtn = Math.max(3, Math.floor(cfg.nodes * 0.03));
    const nHill = Math.max(5, Math.floor(cfg.nodes * 0.15));
    const nValley = cfg.nodes - nMtn - nHill;
    let nid = 0;
    // Mountains — ring around center
    for (let i = 0; i < nMtn; i++) {
      const angle = (i / nMtn) * PI2;
      const r = cfg.area * 0.30;
      nodes.push({
        id: nid++,
        x: Math.max(0, Math.min(cfg.area, cfg.area/2 + r*Math.cos(angle) + rng.gauss(0, cfg.area*0.03))),
        y: Math.max(0, Math.min(cfg.area, cfg.area/2 + r*Math.sin(angle) + rng.gauss(0, cfg.area*0.03))),
        cluster: -1, border: false, battery: 100, mobile: false, speed: 0, heading: 0,
        isS5: false, neighbors: {}, tier: 'mountain', txRange: cfg.area * 0.9,
      });
    }
    // Hills — near populated areas
    const hillCenters = [[0.3,0.3],[0.7,0.4],[0.5,0.7],[0.2,0.6],[0.6,0.65]];
    for (let i = 0; i < nHill; i++) {
      const [cx, cy] = rng.choice(hillCenters);
      nodes.push({
        id: nid++,
        x: Math.max(0, Math.min(cfg.area, cx*cfg.area + rng.gauss(0, cfg.area*0.06))),
        y: Math.max(0, Math.min(cfg.area, cy*cfg.area + rng.gauss(0, cfg.area*0.06))),
        cluster: -1, border: false, battery: rng.uniform(70, 100), mobile: false, speed: 0, heading: 0,
        isS5: false, neighbors: {}, tier: 'hill', txRange: cfg.area * 0.20,
      });
    }
    // Valley — dense urban clusters
    const valCenters = [[0.35,0.35],[0.65,0.45],[0.5,0.65],[0.25,0.5],[0.7,0.55],[0.45,0.45]];
    for (let i = 0; i < nValley; i++) {
      const [cx, cy] = rng.choice(valCenters);
      const isIndoor = rng.uniform(0,1) < 0.2;
      nodes.push({
        id: nid++,
        x: Math.max(0, Math.min(cfg.area, cx*cfg.area + rng.gauss(0, cfg.area*0.04))),
        y: Math.max(0, Math.min(cfg.area, cy*cfg.area + rng.gauss(0, cfg.area*0.04))),
        cluster: -1, border: false, battery: rng.uniform(30, 90), mobile: false, speed: 0, heading: 0,
        isS5: false, neighbors: {}, tier: 'valley', txRange: isIndoor ? cfg.area * 0.015 : cfg.area * 0.05,
      });
    }
  } else {
    for (let i = 0; i < cfg.nodes; i++) {
      nodes.push({
        id: i, x: rng.uniform(0, cfg.area), y: rng.uniform(0, cfg.area),
        cluster: -1, border: false, battery: rng.uniform(50, 100),
        mobile: false, speed: 0, heading: 0, isS5: false, neighbors: {},
      });
    }
  }

  // Mobile nodes
  if (cfg.mobile > 0) {
    const nMobile = Math.max(1, Math.floor(cfg.nodes * cfg.mobile));
    const mobileIds = rng.sample(nodes.map(n => n.id), nMobile);
    for (const id of mobileIds) {
      nodes[id].mobile = true;
      nodes[id].speed = rng.uniform(0.5, 2);
      nodes[id].heading = rng.uniform(0, PI2);
    }
  }

  // Mixed-mode: assign S5 capability to a fraction of nodes
  const s5ratio = cfg.s5ratio || 0;
  if (s5ratio > 0) {
    const nS5 = Math.max(1, Math.floor(cfg.nodes * s5ratio));
    const s5Ids = rng.sample(nodes.map(n => n.id), nS5);
    for (const id of s5Ids) nodes[id].isS5 = true;
  }

  // Create links (supports per-node TX ranges for bay_area topology)
  const isBayArea = cfg.placement === 'bay_area';
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
      const dist = Math.sqrt(dx*dx + dy*dy);
      const rangeI = (isBayArea && nodes[i].txRange) ? nodes[i].txRange : cfg.range;
      const rangeJ = (isBayArea && nodes[j].txRange) ? nodes[j].txRange : cfg.range;
      if (dist <= Math.max(rangeI, rangeJ)) {
        // Asymmetric quality: each node's reach depends on its own range
        let qIJ = Math.max(0, 1 - (dist / rangeI) * (dist / rangeI));
        let qJI = Math.max(0, 1 - (dist / rangeJ) * (dist / rangeJ));
        // Penalize heavily if beyond own range
        if (dist > rangeI) qIJ *= 0.05;
        if (dist > rangeJ) qJI *= 0.05;
        const quality = (qIJ + qJI) / 2;
        if (quality > 0.01) {
          links.push({ a: i, b: j, dist, quality, alive: true });
          nodes[i].neighbors[j] = qIJ;
          nodes[j].neighbors[i] = qJI;
        }
      }
    }
  }

  // Kill nodes
  if (cfg.kills > 0) {
    const nKill = Math.floor(nodes.length * cfg.kills);
    const killIds = rng.sample(nodes.map(n=>n.id), nKill);
    for (const id of killIds) {
      nodes[id].battery = 0;
      for (const l of links) {
        if (l.a === id || l.b === id) l.alive = false;
      }
    }
  }

  // Degrade links
  if (cfg.degrade > 0) {
    const nDeg = Math.floor(links.length * cfg.degrade);
    const targets = rng.sample(links.filter(l=>l.alive), nDeg);
    for (const l of targets) l.quality *= rng.uniform(0.1, 0.5);
  }

  // Simple quadrant clustering
  const half = cfg.area / 2;
  for (const n of nodes) {
    n.cluster = (n.x < half ? 0 : 1) + (n.y < half ? 0 : 2);
  }

  // Push clusters apart for visual clarity (add gap between quadrants)
  const gap = cfg.area * 0.08; // 8% gap
  for (const n of nodes) {
    if (n.x >= half) n.x += gap; else n.x -= gap;
    if (n.y >= half) n.y += gap; else n.y -= gap;
  }
  // Expand area to account for gap
  const expandedArea = cfg.area + gap * 2;

  // Border detection
  for (const n of nodes) {
    for (const nid of Object.keys(n.neighbors)) {
      if (nodes[+nid].cluster !== n.cluster) { n.border = true; break; }
    }
  }

  // Inter-cluster: only 2 bridge links per cluster pair exist (dedicated routes).
  // All other inter-cluster links are physically removed — they don't exist.
  const bridgeLinks = new Set(); // edge indices that are bridge links
  const interByPair = {}; // "c0-c1" -> [{index, quality}]
  for (let i = 0; i < links.length; i++) {
    const l = links[i];
    if (!l.alive) continue;
    const ca = nodes[l.a].cluster, cb = nodes[l.b].cluster;
    if (ca === cb) continue;
    const pairKey = Math.min(ca, cb) + '-' + Math.max(ca, cb);
    if (!interByPair[pairKey]) interByPair[pairKey] = [];
    interByPair[pairKey].push({ idx: i, quality: l.quality });
  }
  // Keep top 2 per pair, DELETE the rest (remove link + neighbor entries)
  const toDelete = new Set();
  for (const [pairKey, candidates] of Object.entries(interByPair)) {
    candidates.sort((a, b) => b.quality - a.quality);
    for (let j = 0; j < candidates.length; j++) {
      if (j < 2) {
        bridgeLinks.add(candidates[j].idx);
      } else {
        toDelete.add(candidates[j].idx);
      }
    }
  }
  // Remove deleted links and their neighbor references
  for (const idx of toDelete) {
    const l = links[idx];
    l.alive = false; // mark dead so it's skipped everywhere
    delete nodes[l.a].neighbors[l.b];
    delete nodes[l.b].neighbors[l.a];
  }

  // Compute cluster bounding boxes for background drawing
  const clusterBounds = {};
  for (const n of nodes) {
    if (n.battery <= 0) continue;
    if (!clusterBounds[n.cluster]) {
      clusterBounds[n.cluster] = { minX: n.x, maxX: n.x, minY: n.y, maxY: n.y };
    } else {
      const b = clusterBounds[n.cluster];
      b.minX = Math.min(b.minX, n.x); b.maxX = Math.max(b.maxX, n.x);
      b.minY = Math.min(b.minY, n.y); b.maxY = Math.max(b.maxY, n.y);
    }
  }

  return { nodes, links, area: expandedArea, range: cfg.range, cfg, clusterBounds, bridgeLinks };
}

