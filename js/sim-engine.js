// MeshRoute Simulator - Engine (State, UI, Animation Loop)

// ---- Simulation State Machine ----
const simState = {
  running: false,
  finished: false,
  started: false,    // true once first Run/Step — locks endpoint selection
  speed: 1,
  scenario: 'medium',
  msgCount: 20,
  messages: [],      // [{src, dst}]
  msgIndex: 0,
  pickedSrc: -1,     // user-selected source node (-1 = not set)
  pickedDst: -1,     // user-selected destination node (-1 = not set)
  networkBuilt: false, // true after build animation
  generation: 0,     // increments on reset — stale setTimeout callbacks check this
};

let rendererManaged, rendererSystem5;

function init() {
  rendererManaged = new SimRenderer('canvas-managed', 'managed');
  rendererSystem5 = new SimRenderer('canvas-system5', 'system5');

  // Link renderers for synced pan/zoom
  rendererManaged.syncPeer = rendererSystem5;
  rendererSystem5.syncPeer = rendererManaged;

  // Read initial values from DOM
  simState.scenario = document.getElementById('scenario-select').value;
  simState.msgCount = +document.getElementById('msg-count').value || 20;
  simState.speed = +document.getElementById('speed-slider').value / 4;
  document.getElementById('speed-label').textContent = simState.speed.toFixed(1) + 'x';

  resetSim();

  // --- Control Bindings ---
  document.getElementById('btn-build').addEventListener('click', buildNetworkAnimated);
  document.getElementById('btn-start').addEventListener('click', toggleRun);
  document.getElementById('btn-step').addEventListener('click', stepOne);
  document.getElementById('btn-stop').addEventListener('click', stopSim);
  document.getElementById('btn-reset').addEventListener('click', resetSim);

  document.getElementById('scenario-select').addEventListener('change', e => {
    simState.scenario = e.target.value;
    resetSim();
  });

  const msgInput = document.getElementById('msg-count');
  const updateMsgCount = () => {
    simState.msgCount = Math.max(1, Math.min(200, +msgInput.value || 20));
  };
  msgInput.addEventListener('input', updateMsgCount);
  msgInput.addEventListener('change', updateMsgCount);

  document.getElementById('speed-slider').addEventListener('input', e => {
    simState.speed = +e.target.value / 4;
    document.getElementById('speed-label').textContent = simState.speed.toFixed(1) + 'x';
  });

  // --- Click-to-select SRC/DST (on both canvases) ---
  for (const renderer of [rendererManaged, rendererSystem5]) {
    renderer.canvas.addEventListener('click', e => handleNodeClick(e, renderer));
  }

  // Handle window resize
  window.addEventListener('resize', () => {
    rendererManaged.resize();
    rendererSystem5.resize();
    if (rendererManaged.net) rendererManaged.fitNetwork();
    if (rendererSystem5.net) rendererSystem5.fitNetwork();
  });

  requestAnimationFrame(loop);
}

function handleNodeClick(e, renderer) {
  // Only allow picking before simulation has started
  if (simState.started) return;
  if (!renderer.net) return;

  const rect = renderer.canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;

  // Find closest node to click position
  let bestId = -1, bestDist = Infinity;
  const hitRadius = 15; // pixels
  for (const n of renderer.net.nodes) {
    if (n.battery <= 0) continue;
    if (Object.keys(n.neighbors).length === 0) continue;
    const [sx, sy] = renderer.toScreen(n.x, n.y);
    const dx = mx - sx, dy = my - sy;
    const d = Math.sqrt(dx*dx + dy*dy);
    if (d < hitRadius && d < bestDist) {
      bestDist = d;
      bestId = n.id;
    }
  }
  if (bestId < 0) return;

  const cfg = SCENARIOS[simState.scenario];
  const isBroadcast = cfg.broadcastMode || false;

  if (isBroadcast) {
    // Broadcast mode: only SRC needed
    simState.pickedSrc = bestId;
    simState.pickedDst = -1; // no destination
  } else if (simState.pickedSrc < 0) {
    simState.pickedSrc = bestId;
  } else if (simState.pickedDst < 0) {
    if (bestId === simState.pickedSrc) return;
    simState.pickedDst = bestId;
    generateMessages();
  } else {
    simState.pickedSrc = bestId;
    simState.pickedDst = -1;
    simState.messages = [];
  }

  updateEndpointDisplay();
  updateMarkers();
}

function updateEndpointDisplay() {
  const el = document.getElementById('endpoint-display');
  const cfg = SCENARIOS[simState.scenario];
  const isBroadcast = cfg.broadcastMode || false;

  if (isBroadcast) {
    if (simState.pickedSrc < 0) {
      el.innerHTML = 'Click a node to set <span style="color:#4ade80">broadcast SRC</span>';
    } else {
      el.innerHTML = `<span style="color:#4ade80">SRC: Node ${simState.pickedSrc}</span> <span style="color:var(--text-dim)">→ ALL nodes</span>`;
    }
  } else if (simState.pickedSrc < 0) {
    el.innerHTML = 'Click a node to set <span style="color:#4ade80">SRC</span>';
  } else if (simState.pickedDst < 0) {
    el.innerHTML = `<span style="color:#4ade80">SRC: Node ${simState.pickedSrc}</span> — now click <span style="color:#f87171">DST</span>`;
  } else {
    el.innerHTML = `<span style="color:#4ade80">SRC: ${simState.pickedSrc}</span> <span style="color:var(--text-dim)">→</span> <span style="color:#f87171">DST: ${simState.pickedDst}</span>`;
    if (!simState.started) {
      el.innerHTML += ` <span style="color:var(--text-muted)">(click nodes to change)</span>`;
    }
  }
  if (simState.started) {
    el.innerHTML += ` <span style="color:var(--text-muted)">(locked)</span>`;
  }
}

function updateMarkers() {
  rendererManaged.markedSrc = simState.pickedSrc;
  rendererManaged.markedDst = simState.pickedDst;
  rendererSystem5.markedSrc = simState.pickedSrc;
  rendererSystem5.markedDst = simState.pickedDst;
}

function generateMessages() {
  simState.messages = [];
  if (simState.pickedSrc < 0 || simState.pickedDst < 0) return;
  // All messages go between the selected SRC and DST
  for (let i = 0; i < simState.msgCount; i++) {
    simState.messages.push({ src: simState.pickedSrc, dst: simState.pickedDst });
  }
}

function stopSim() {
  simState.running = false;
  simState.generation++; // invalidate all pending setTimeout callbacks
  autoDispatchTimer = 0;

  // Clear in-flight animations
  rendererManaged.animPackets = [];
  rendererSystem5.animPackets = [];

  document.getElementById('btn-start').textContent = simState.msgIndex > 0 ? 'Continue' : 'Run';
}

function resetSim() {
  simState.running = false;
  simState.finished = false;
  simState.started = false;
  simState.networkBuilt = false;
  simState.msgIndex = 0;
  simState.pickedSrc = -1;
  simState.pickedDst = -1;
  simState.generation++; // kill all pending setTimeout callbacks
  autoDispatchTimer = 0;

  const btn = document.getElementById('btn-start');
  btn.textContent = 'Run';
  btn.classList.add('primary');
  btn.disabled = false;
  document.getElementById('btn-step').disabled = false;
  document.getElementById('btn-build').disabled = false;

  // Clear log
  document.getElementById('sim-log').innerHTML = '';

  // Re-read message count from input
  simState.msgCount = Math.max(1, Math.min(200, +document.getElementById('msg-count').value || 20));

  // Invalidate route cache when scenario changes
  if (typeof walkFloodReset === 'function') walkFloodReset();
  else if (typeof echoRouteReset === 'function') echoRouteReset();

  // Build full network immediately (visible from start)
  const netManaged = buildNetwork(simState.scenario, new RNG(42));
  const netSystem5 = buildNetwork(simState.scenario, new RNG(42));
  rendererManaged.setNetwork(netManaged);
  rendererSystem5.setNetwork(netSystem5);

  // Pick random SRC/DST from alive nodes in different clusters for interesting paths
  const alive = netManaged.nodes.filter(n => n.battery > 0 && Object.keys(n.neighbors).length > 0);
  if (alive.length >= 2) {
    const pickRng = new RNG(Date.now() % 100000); // slightly random each reset
    const src = pickRng.choice(alive);
    // Try to pick DST in a different cluster for a cross-cluster path
    const otherCluster = alive.filter(n => n.cluster !== src.cluster && n.id !== src.id);
    const dst = otherCluster.length > 0 ? pickRng.choice(otherCluster) : pickRng.choice(alive.filter(n => n.id !== src.id));
    simState.pickedSrc = src.id;
    simState.pickedDst = dst.id;
    generateMessages();
  }
  updateEndpointDisplay();
  updateMarkers();

  // Write initial network info to log
  const cfg = SCENARIOS[simState.scenario];
  const nAlive = alive.length;
  const nLinks = netManaged.links.filter(l => l.alive).length;
  const nBorder = netManaged.nodes.filter(n => n.border).length;
  const clusters = {};
  for (const n of netManaged.nodes) {
    if (n.battery <= 0) continue;
    clusters[n.cluster] = (clusters[n.cluster] || 0) + 1;
  }
  const log = document.getElementById('sim-log');
  log.innerHTML = `<div class="log-step">
    <div class="log-header">Network Ready</div>
    <div class="log-dim">${nAlive} nodes, ${nLinks} links, ${Object.keys(clusters).length} clusters, ${nBorder} border nodes.
    Terrain: ${cfg.terrain}, Range: ${cfg.range}m, Area: ${(cfg.area/1000).toFixed(1)}km</div>
    <div style="margin-top:0.3rem;color:var(--cyan);">SRC and DST randomly selected — <b>click any node</b> to change them before starting.</div>
  </div>`;
}

function buildNetworkAnimated() {
  if (simState.networkBuilt) return;
  simState.networkBuilt = true;
  document.getElementById('btn-build').disabled = true;

  const net = rendererManaged.net;
  if (!net) return;

  const cfg = SCENARIOS[simState.scenario];
  const log = document.getElementById('sim-log');
  log.innerHTML = ''; // clear

  const steps = [];
  const nAlive = net.nodes.filter(n => n.battery > 0).length;
  const nLinks = net.links.filter(l => l.alive).length;
  const nBorder = net.nodes.filter(n => n.border).length;
  const clusters = {};
  for (const n of net.nodes) {
    if (n.battery <= 0) continue;
    clusters[n.cluster] = (clusters[n.cluster] || 0) + 1;
  }
  const nDead = net.nodes.filter(n => n.battery <= 0).length;
  const nMobile = net.nodes.filter(n => n.mobile).length;
  const avgNeighbors = (net.nodes.reduce((s, n) => s + Object.keys(n.neighbors).length, 0) / Math.max(nAlive, 1)).toFixed(1);

  // Step 1: Node placement
  steps.push({
    title: 'Step 1: Node Placement',
    html: `<span class="log-good">${cfg.nodes} nodes</span> placed using <b>${cfg.placement}</b> strategy `
      + `in a <b>${(cfg.area/1000).toFixed(1)}km</b> area.`
      + (nMobile > 0 ? `<br><span class="log-dim">${nMobile} mobile nodes (walking speed).</span>` : '')
      + (nDead > 0 ? `<br><span class="log-bad">${nDead} nodes dead (0% battery — disaster scenario).</span>` : ''),
  });

  // Step 2: Link discovery
  steps.push({
    title: 'Step 2: Link Discovery (LoRa Range)',
    html: `Each node scans for neighbors within <b>${cfg.range}m</b> LoRa range (${cfg.terrain} terrain).`
      + `<br><span class="log-good">${nLinks} links</span> established. `
      + `Average <b>${avgNeighbors}</b> neighbors per node.`
      + `<br><span class="log-dim">Link quality = 1 - (distance/range)², degraded links have lower quality.</span>`,
  });

  // Step 3: Clustering
  const clusterList = Object.entries(clusters).map(([c, n]) => `C${c}: ${n} nodes`).join(', ');
  steps.push({
    title: 'Step 3: Geo-Clustering (Quadrant Split)',
    html: `Nodes grouped into <span class="log-good">${Object.keys(clusters).length} clusters</span> by geographic quadrant.`
      + `<br>${clusterList}`
      + `<br><span class="log-dim">In System 5, messages are routed cluster-by-cluster. In Managed Flooding, clusters are ignored — everything floods.</span>`,
  });

  // Step 4: Border nodes
  steps.push({
    title: 'Step 4: Border Node Election',
    html: `<span class="log-good">${nBorder} border nodes</span> detected (nodes with neighbors in other clusters).`
      + `<br><span class="log-dim">Border nodes are the gateways between clusters — System 5 routes inter-cluster traffic through them. `
      + `Shown with white rings on the map.</span>`,
  });

  // Step 5: Hop limits
  steps.push({
    title: 'Step 5: Meshtastic Hop Limit (the problem)',
    html: `<span class="log-managed">Meshtastic limits messages to <b>3-7 hops</b></span> to prevent broadcast storms.`
      + `<br>Each hop triggers <b>O(n)</b> transmissions (every reachable node rebroadcasts).`
      + `<br>Without hop limit, a single message would flood the entire network repeatedly.`
      + `<br><br><span class="log-system5">System 5 has <b>no hop limit</b></span> — each hop costs only <b>1 TX</b> (directed routing), `
      + `so 20 hops cost less than Managed Flooding costs for 1 hop.`
      + `<br><br><span class="log-dim">Select SRC and DST to see this difference live. `
      + `On the left panel, dashed orange rings show where Meshtastic's hop limit cuts off.</span>`,
  });

  // Animate steps with delay (generation-guarded)
  const buildGen = simState.generation;
  steps.forEach((step, i) => {
    setTimeout(() => {
      if (simState.generation !== buildGen) return;
      const div = document.createElement('div');
      div.className = 'log-step';
      div.innerHTML = `<div class="log-header">${step.title}</div><div>${step.html}</div>`;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    }, i * 800);
  });
}

function ensureStartable() {
  const cfg = SCENARIOS[simState.scenario];
  const isBroadcast = cfg.broadcastMode || false;

  if (isBroadcast) {
    // Broadcast mode: only SRC needed, no DST
    if (simState.pickedSrc < 0) {
      document.getElementById('endpoint-display').innerHTML =
        '<span style="color:#f87171">Click a node to set broadcast SRC!</span>';
      return false;
    }
  } else {
    if (simState.pickedSrc < 0 || simState.pickedDst < 0) {
      document.getElementById('endpoint-display').innerHTML =
        '<span style="color:#f87171">Select SRC and DST nodes first!</span>';
      return false;
    }
  }
  if (!simState.started) {
    simState.started = true;
    updateEndpointDisplay();
    if (isBroadcast) {
      prepareHopByHopBroadcast();
    } else {
      prepareHopByHop();
    }
  }
  return true;
}

// Pre-compute both routing results and group Managed Flooding TX events by hop
function prepareHopByHop() {
  const src = simState.pickedSrc, dst = simState.pickedDst;
  const cfg = SCENARIOS[simState.scenario];
  const isMixed = (cfg.s5ratio || 0) > 0;

  // --- LEFT SIDE: Always Managed Flooding ---
  const mResult = simulateManagedFlood(rendererManaged.net, src, dst, new RNG(42));
  const mHopGroups = {};
  let mMaxHop = 0;
  for (const ev of mResult.txEvents) {
    if (!mHopGroups[ev.hop]) mHopGroups[ev.hop] = [];
    mHopGroups[ev.hop].push(ev);
    if (ev.hop > mMaxHop) mMaxHop = ev.hop;
  }

  // --- RIGHT SIDE: System 5 (pure) or Dual-Mode (mixed) ---
  let s5Path = null, dualResult = null, s5Hops = 0;
  let rightTitle = 'System 5', rightIntro = '';

  if (isMixed) {
    // Dual-mode simulation
    dualResult = simulateDualMode(rendererSystem5.net, src, dst, new RNG(42));
    rightTitle = `Dual-Mode (${Math.round(cfg.s5ratio*100)}% S5)`;

    if (dualResult.mode === 'direct' && dualResult.path) {
      s5Path = dualResult.path;
      s5Hops = dualResult.s5Hops;
      const srcS5 = rendererSystem5.net.nodes[src].isS5 ? 'S5' : 'Legacy';
      const dstS5 = rendererSystem5.net.nodes[dst].isS5 ? 'S5' : 'Legacy';
      rightIntro = `Both nodes are S5-capable. Direct S5 route found: <span class="log-path">${s5Path.join(' → ')}</span> (${s5Hops} hops).<br>`
        + `<span class="log-dim">All intermediate nodes are S5 — no flooding needed. Pure directed routing.</span>`;
    } else {
      // Mixed flooding with S5 suppression
      // Group dual-mode events by hop for animation
      const dHopGroups = {};
      let dMaxHop = 0;
      for (const ev of dualResult.txEvents) {
        if (!dHopGroups[ev.hop]) dHopGroups[ev.hop] = [];
        dHopGroups[ev.hop].push(ev);
        if (ev.hop > dMaxHop) dMaxHop = ev.hop;
      }
      // Store for animation
      dualResult._hopGroups = dHopGroups;
      dualResult._maxHop = dMaxHop;
      s5Hops = dMaxHop + 1;

      const srcS5 = rendererSystem5.net.nodes[src].isS5 ? 'S5' : 'Legacy';
      const dstS5 = rendererSystem5.net.nodes[dst].isS5 ? 'S5' : 'Legacy';
      const nS5 = rendererSystem5.net.nodes.filter(n => n.isS5).length;
      rightIntro = `SRC is <b>${srcS5}</b>, DST is <b>${dstS5}</b>. No all-S5 path exists.<br>`
        + `Falling back to <b>hybrid flooding</b>: ${nS5} S5 nodes send <b>directed</b> (1 TX) where they know the next hop, Legacy nodes flood normally.<br>`
        + `<span class="log-dim">S5 nodes use their routing table to skip broadcasting — they send only to the next hop on the path. Legacy nodes still flood to ALL neighbors. Same suppression rate (40%) for full backward compatibility.</span>`;
    }
  } else {
    // Check which right-panel router is selected
    const rightRouterSel = document.getElementById('right-router');
    const rightRouterVal = rightRouterSel ? rightRouterSel.value : 'system5';

    if (rightRouterVal === 'walkflood') {
      // WalkFlood — Passive Learning + Walk + Mini-Flood
      const wfResult = simulateWalkFlood(rendererSystem5.net, src, dst, new RNG(42));
      s5Path = wfResult.path;
      s5Hops = s5Path ? s5Path.length - 1 : 0;
      rightTitle = 'WalkFlood';
      const phase = wfResult.phase || 'unknown';

      if (wfResult.delivered && s5Path) {
        const phaseLabel = phase === 'directed' ? 'Directed (learned route)'
          : phase === 'walk' ? 'Walk (neighbor exploration)'
          : phase === 'walk+direct' ? 'Walk → Directed'
          : phase === 'flood (learning)' ? 'Managed Flood (still learning)'
          : 'Mini-Flood (last resort)';
        const isLearning = phase === 'flood (learning)';
        rightIntro = `Delivered via <b>${phaseLabel}</b>: <span class="log-path">${s5Path.join(' → ')}</span> (${s5Hops} hops, ${wfResult.totalTx} TX).`
          + (isLearning
            ? `<br><span class="log-dim">Early phase: using managed flooding like the left panel. Routes are being learned from this delivery. After ~10 messages, WalkFlood switches to directed routing.</span>`
            : `<br><span class="log-dim">WalkFlood: learned route → directed forwarding. No flooding needed. <span style="color:#a78bfa">Purple nodes</span> = upgraded to WalkFlood.</span>`);
      } else if (!wfResult.delivered && wfResult.phase === 'flood (learning)') {
        rightIntro = `<span class="log-bad">Managed flood failed (learning phase) — ${wfResult.totalTx} TX.</span><br>`
          + `<span class="log-dim">Still in learning phase — flooding like the left panel. Routes will improve with more messages.</span>`;
      } else {
        rightIntro = `<span class="log-bad">All 4 phases failed — packet dropped (${wfResult.totalTx} TX spent).</span><br>`
          + `<span class="log-dim">WalkFlood tried: directed routing, walking toward destination, and mini-flood. No reachable path found.</span>`;
      }
    } else if (rightRouterVal === 'echoroute') {
      // EchoRoute — zero-overhead directed routing via passive learning
      const erResult = simulateEchoRoute(rendererSystem5.net, src, dst, new RNG(42));
      s5Path = erResult.path;
      s5Hops = s5Path ? s5Path.length - 1 : 0;
      rightTitle = 'EchoRoute';

      if (erResult.delivered && s5Path) {
        rightIntro = `Route learned passively: <span class="log-path">${s5Path.join(' → ')}</span> (${s5Hops} hops).`
          + (erResult.retries > 0 ? ` (${erResult.retries} retries on lossy links)` : '')
          + `<br><span class="log-dim">EchoRoute learns routes by overhearing traffic — zero control packets. Each hop is 1 directed TX. No flooding.</span>`;
      } else {
        rightIntro = `<span class="log-bad">No learned route to DST — packet dropped.</span><br>`
          + `<span class="log-dim">EchoRoute never floods. If no route is known from passive learning, the packet is dropped.</span>`;
      }
    } else {
      // Pure System 5 — simulate with multi-path + fallback
      const s5Result = simulateSystem5(rendererSystem5.net, src, dst, new RNG(42));
      s5Path = s5Result.path;
      s5Hops = s5Path ? s5Path.length - 1 : 0;

      if (s5Result.fallback) {
        const s5HopGroups = {};
        let s5MaxHop = 0;
        for (const ev of s5Result.txEvents) {
          const h = ev.hop || 0;
          if (!s5HopGroups[h]) s5HopGroups[h] = [];
          s5HopGroups[h].push(ev);
          if (h > s5MaxHop) s5MaxHop = h;
        }
        dualResult = { mode: 'mixed', _hopGroups: s5HopGroups, _maxHop: s5MaxHop,
                       txEvents: s5Result.txEvents, delivered: s5Result.delivered,
                       totalTx: s5Result.totalTx };
        s5Hops = s5MaxHop + 1;
        rightTitle = 'System 5 (fallback flood)';
        rightIntro = `All direct routes failed — using <b>scoped cluster flooding</b> as fallback.<br>`
          + `<span class="log-dim">Flooding only in SRC + DST clusters + border nodes. Much less TX than full-network flooding.</span>`;
      } else if (s5Path) {
        rightIntro = `Route found: <span class="log-path">${s5Path.join(' → ')}</span> (${s5Hops} hops).`
          + (s5Result.retries > 0 ? ` (${s5Result.retries} retries needed)` : '')
          + `<br><span class="log-dim">The routing table was built during cluster formation. Each node knows the best path to every other node via border nodes between clusters. Up to 3 alternative paths are tried before fallback flooding.</span>`;
      } else {
        rightIntro = `<span class="log-bad">No route found — even fallback flooding couldn't reach DST.</span>`;
      }
    }
  }

  const totalHops = Math.max(mMaxHop + 1, s5Hops);

  simState.hopPlan = {
    isMixed,
    mResult, mHopGroups, mMaxHop,
    s5Path,
    s5Hops,
    dualResult,
    totalHops,
    currentHop: -1,
    mTotalTxSoFar: 0,
    s5TotalTxSoFar: 0,
    mDelivered: false,
    s5Delivered: false,
    rightTitle,
  };

  // Mark SRC
  rendererManaged.clearReached();
  rendererSystem5.clearReached();
  rendererManaged.markReached(src);
  rendererSystem5.markReached(src);

  // Update right panel title for mixed mode
  const titleEl = document.querySelector('.sim-panel-title.system5');
  if (titleEl) titleEl.textContent = rightTitle || (isMixed ? rightTitle : 'System 5 (MeshRoute)');

  // Write initial log
  const srcNode = rendererManaged.net.nodes[src];
  const dstNode = rendererManaged.net.nodes[dst];
  const log = document.getElementById('sim-log');
  log.innerHTML = '';

  const introDiv = document.createElement('div');
  introDiv.className = 'log-step';
  introDiv.innerHTML = `<div class="log-header">Sending: Node ${src} (C${srcNode.cluster}) → Node ${dst} (C${dstNode.cluster})</div>
    <div class="log-columns">
      <div class="log-col left">
        <div class="log-col-title log-managed">Managed Flooding (Legacy Only)</div>
        No routing table. Node ${src} doesn't know where Node ${dst} is.<br>
        <b>Only option: broadcast to ALL neighbors</b> and hope it arrives.<br>
        Hop limit: <b>${mResult.hopLimit}</b> — after that, packet is dropped.
      </div>
      <div class="log-col right">
        <div class="log-col-title log-system5">${rightTitle}</div>
        ${isMixed
          ? `Node ${src} checks: am I S5-capable? ${rendererSystem5.net.nodes[src].isS5 ? 'Yes' : 'No'}.<br>${rightIntro}`
          : `Node ${src} looks up Node ${dst} in its <b>routing table</b>.<br>${rightIntro}`}
      </div>
    </div>
    <div class="log-dim" style="margin-top:0.3rem;">Press <b>Step</b> to advance one hop at a time, or <b>Run</b> for auto-play.${isMixed ? '<br>Nodes with <b>white diamond</b> = S5-capable. Others = Legacy (flood only).' : ''}</div>`;
  log.appendChild(introDiv);
  log.scrollTop = log.scrollHeight;
}

// ---- Broadcast Mode: prepare + advance ----

function prepareHopByHopBroadcast() {
  const src = simState.pickedSrc;

  const mResult = simulateManagedBroadcast(rendererManaged.net, src, new RNG(42));
  const cdResult = simulateClusterDistributorBroadcast(rendererSystem5.net, src, new RNG(42));

  const totalHops = Math.max(mResult.maxHop + 1, cdResult.maxHop + 1);

  simState.hopPlan = {
    isBroadcast: true,
    mResult, mHopGroups: mResult.hopGroups, mMaxHop: mResult.maxHop,
    cdResult, cdHopGroups: cdResult.hopGroups, cdMaxHop: cdResult.maxHop,
    totalHops,
    currentHop: -1,
    mTotalTxSoFar: 0, s5TotalTxSoFar: 0,
    mReached: new Set([src]), cdReached: new Set([src]),
    s5Hops: cdResult.maxHop + 1,
  };

  // Update panel titles
  const titleL = document.querySelector('.sim-panel-title.managed');
  const titleR = document.querySelector('.sim-panel-title.system5');
  if (titleL) titleL.textContent = 'Managed Flooding (Broadcast)';
  if (titleR) titleR.textContent = 'Cluster-Distributor (Broadcast)';

  // Update stat labels
  const limL = document.getElementById('limit-managed');
  const limR = document.getElementById('limit-system5');
  if (limL) limL.textContent = '7 hops';
  if (limR) limR.textContent = 'wave';

  rendererManaged.clearReached();
  rendererSystem5.clearReached();
  rendererManaged.markReached(src);
  rendererSystem5.markReached(src);

  const srcNode = rendererManaged.net.nodes[src];
  const log = document.getElementById('sim-log');
  log.innerHTML = '';

  const introDiv = document.createElement('div');
  introDiv.className = 'log-step';
  introDiv.innerHTML = `<div class="log-header">Broadcast from Node ${src} (C${srcNode.cluster}) to ALL nodes</div>
    <div class="log-columns">
      <div class="log-col left">
        <div class="log-col-title log-managed">Managed Flooding</div>
        Node ${src} broadcasts to ALL neighbors. Each neighbor rebroadcasts to all of theirs.<br>
        <span class="log-dim">Every node that hears rebroadcasts -- O(n) transmissions per message.</span>
      </div>
      <div class="log-col right">
        <div class="log-col-title log-system5">Cluster-Distributor</div>
        Node ${src} sends to cluster distributor (valley node). Distributor floods locally, border nodes relay to next cluster.<br>
        <span class="log-dim">Wave propagation: flood small clusters, relay between them. ${Object.keys(cdResult.distributors).length} distributors elected.</span>
      </div>
    </div>
    <div class="log-dim" style="margin-top:0.3rem;">Goal: reach ALL ${mResult.totalAlive} alive nodes with minimum TX. Press <b>Step</b> or <b>Run</b>.</div>`;
  log.appendChild(introDiv);
  log.scrollTop = log.scrollHeight;
}

function advanceOneHopBroadcast() {
  const hp = simState.hopPlan;
  if (!hp) return;

  hp.currentHop++;
  const hop = hp.currentHop;
  const gen = simState.generation;
  const log = document.getElementById('sim-log');

  const mDone = hop > hp.mMaxHop;
  const cdDone = hop > hp.cdMaxHop;
  if (mDone && cdDone) { markFinishedBroadcast(); return; }

  rendererManaged.fadeReached();
  rendererSystem5.fadeReached();

  // --- Left: Managed Flooding ---
  const mEvents = (hp.mHopGroups[hop] || []);
  hp.mTotalTxSoFar += mEvents.length;

  for (const ev of mEvents) {
    rendererManaged.markReached(ev.from);
    rendererManaged.addPacket(ev.from, ev.to, '#fb923c', () => {
      if (simState.generation !== gen) return;
      rendererManaged.markReached(ev.to);
      rendererManaged.markEdgeReached(ev.from, ev.to);
      hp.mReached.add(ev.to);
    });
  }

  // --- Right: Cluster-Distributor ---
  const cdEvents = (hp.cdHopGroups[hop] || []);
  hp.s5TotalTxSoFar += cdEvents.length;

  for (const ev of cdEvents) {
    rendererSystem5.markReached(ev.from);
    const color = ev.mode === 'unicast' || ev.mode === 'bridge' ? '#22d3ee' : '#4ade80';
    rendererSystem5.addPacket(ev.from, ev.to, color, () => {
      if (simState.generation !== gen) return;
      rendererSystem5.markReached(ev.to);
      rendererSystem5.markEdgeReached(ev.from, ev.to);
      hp.cdReached.add(ev.to);
    });
  }

  // Log
  const mReachPct = (hp.mResult.nodesReached.size / hp.mResult.totalAlive * 100).toFixed(1);
  const cdReachPct = (hp.cdResult.nodesReached.size / hp.cdResult.totalAlive * 100).toFixed(1);

  let mHtml = mEvents.length > 0
    ? `<span class="log-flood">${mEvents.length} TX</span> this wave.<br><span class="log-dim">Total: ${hp.mTotalTxSoFar} TX. Reach: ${mReachPct}%</span>`
    : `<span class="log-dim">No transmissions.</span>`;

  const cdUnicast = cdEvents.filter(e => e.mode === 'unicast' || e.mode === 'bridge').length;
  const cdFlood = cdEvents.filter(e => e.mode === 'flood').length;
  let cdHtml = cdEvents.length > 0
    ? `<span class="log-path">${cdEvents.length} TX</span>`
      + (cdUnicast > 0 ? ` (${cdUnicast} relay + ${cdFlood} local flood)` : ` (local flood)`)
      + `<br><span class="log-dim">Total: ${hp.s5TotalTxSoFar} TX. Reach: ${cdReachPct}%</span>`
    : `<span class="log-dim">No transmissions.</span>`;

  const stepDiv = document.createElement('div');
  stepDiv.className = 'log-step';
  stepDiv.innerHTML = `<div class="log-header">Wave ${hop + 1}</div>
    <div class="log-columns">
      <div class="log-col left"><div class="log-col-title log-managed">Managed Flood</div>${mHtml}</div>
      <div class="log-col right"><div class="log-col-title log-system5">Cluster-Dist</div>${cdHtml}</div>
    </div>
    ${(hp.mTotalTxSoFar > 0 && hp.s5TotalTxSoFar > 0) ?
      `<div class="log-comparison">TX: Managed <span class="log-flood">${hp.mTotalTxSoFar}</span> vs Cluster-Dist <span class="log-path">${hp.s5TotalTxSoFar}</span> -- <span class="log-savings">${((1 - hp.s5TotalTxSoFar / hp.mTotalTxSoFar) * 100).toFixed(0)}% saved</span></div>` : ''}`;
  log.appendChild(stepDiv);
  log.scrollTop = log.scrollHeight;

  // Stats
  rendererManaged.stats.tx = hp.mTotalTxSoFar;
  rendererManaged.stats.lastHops = hop + 1;
  rendererManaged.stats.sent = 1;
  rendererSystem5.stats.tx = hp.s5TotalTxSoFar;
  rendererSystem5.stats.lastHops = Math.min(hop + 1, hp.cdMaxHop + 1);
  rendererSystem5.stats.sent = 1;

  if ((hop + 1) > hp.mMaxHop && (hop + 1) > hp.cdMaxHop) {
    setTimeout(() => {
      if (simState.generation !== gen) return;
      markFinishedBroadcast();
    }, 500);
  }
}

function markFinishedBroadcast() {
  const hp = simState.hopPlan;
  const log = document.getElementById('sim-log');
  const mReach = hp.mResult.reachPct.toFixed(1);
  const cdReach = hp.cdResult.reachPct.toFixed(1);
  const savings = ((1 - hp.s5TotalTxSoFar / Math.max(hp.mTotalTxSoFar, 1)) * 100).toFixed(0);

  const sumDiv = document.createElement('div');
  sumDiv.className = 'log-step';
  sumDiv.innerHTML = `<div class="log-header">Broadcast Summary</div>
    <div class="log-columns">
      <div class="log-col left">
        <div class="log-col-title log-managed">Managed Flooding</div>
        Reach: <b>${mReach}%</b> (${hp.mResult.nodesReached.size}/${hp.mResult.totalAlive} nodes)<br>
        Total: <b>${hp.mTotalTxSoFar} TX</b>
      </div>
      <div class="log-col right">
        <div class="log-col-title log-system5">Cluster-Distributor</div>
        Reach: <b>${cdReach}%</b> (${hp.cdResult.nodesReached.size}/${hp.cdResult.totalAlive} nodes)<br>
        Total: <b>${hp.s5TotalTxSoFar} TX</b>
      </div>
    </div>
    <div class="log-comparison">
      <b>Cluster-Distributor used ${savings}% fewer TX</b>
      (${(hp.mTotalTxSoFar / Math.max(hp.s5TotalTxSoFar, 1)).toFixed(1)}x more efficient)
      while reaching ${cdReach}% of the network.
    </div>`;
  log.appendChild(sumDiv);
  log.scrollTop = log.scrollHeight;
  markFinished();
}

function toggleRun() {
  if (simState.finished) { resetSim(); return; }
  if (!ensureStartable()) return;
  simState.running = !simState.running;
  const hp = simState.hopPlan;
  const label = simState.running ? 'Pause' : (hp && hp.currentHop >= 0 ? 'Continue' : 'Run');
  document.getElementById('btn-start').textContent = label;
}

function stepOne() {
  if (simState.finished) return;
  if (!ensureStartable()) return;
  const hp = simState.hopPlan;
  if (hp && hp.isBroadcast) {
    advanceOneHopBroadcast();
  } else {
    advanceOneHop();
  }
}

function advanceOneHop() {
  const hp = simState.hopPlan;
  if (!hp) return;

  hp.currentHop++;
  const hop = hp.currentHop;
  const gen = simState.generation;
  const log = document.getElementById('sim-log');

  // Check if both sides are done
  const mDone = hop > hp.mMaxHop;
  const s5Done = hop >= hp.s5Hops;
  if (mDone && s5Done) { markFinished(); return; }

  // Fade previous hop markings on both sides
  rendererManaged.fadeReached();
  rendererSystem5.fadeReached();

  // --- Managed Flooding for this hop ---
  // All visible TX events fire as ONE simultaneous wave (not staggered)
  const mEvents = hp.mHopGroups[hop] || [];
  const mTxThisHop = mEvents.length;
  hp.mTotalTxSoFar += mTxThisHop;
  const mHitLimit = hop >= hp.mResult.hopLimit;
  let mDeliveredThisHop = false;
  let mVisibleTx = 0;

  // First: mark all senders and check delivery
  for (const ev of mEvents) {
    if (ev.to === simState.pickedDst) mDeliveredThisHop = true;
    rendererManaged.markReached(ev.from);
  }

  // Animate all packets as one wave
  for (const ev of mEvents) {
    mVisibleTx++;
    rendererManaged.addPacket(ev.from, ev.to, '#fb923c', () => {
      if (simState.generation !== gen) return;
      rendererManaged.markReached(ev.to);
      rendererManaged.markEdgeReached(ev.from, ev.to);
    });
  }
  if (mDeliveredThisHop && !hp.mDelivered) {
    hp.mDelivered = true;
    // Mark the shortest path as delivery path (green)
    const mPath = bfsPath(rendererManaged.net.nodes, rendererManaged.net.links, simState.pickedSrc, simState.pickedDst);
    if (mPath) rendererManaged.markDeliveryPath(mPath);
  }

  // --- Right side: System 5 or Dual-Mode for this hop ---
  let s5Html = '';
  let s5TxThisHop = 0;

  if (hp.dualResult && hp.dualResult._hopGroups) {
    // Flood-based mode (mixed-mode OR S5 fallback) — animate hop events
    const dEvents = (hp.dualResult._hopGroups || {})[hop] || [];
    s5TxThisHop = dEvents.length;
    hp.s5TotalTxSoFar += s5TxThisHop;
    let dDelivered = false;

    // Animate as wave (like managed flooding)
    for (const ev of dEvents) {
      if (ev.to === simState.pickedDst) dDelivered = true;
      rendererSystem5.markReached(ev.from);
      const pktColor = ev.mode === 's5' ? '#22d3ee' : '#fb923c';
      rendererSystem5.addPacket(ev.from, ev.to, pktColor, () => {
        if (simState.generation !== gen) return;
        rendererSystem5.markReached(ev.to);
        rendererSystem5.markEdgeReached(ev.from, ev.to);
      });
    }
    if (dDelivered && !hp.s5Delivered) {
      hp.s5Delivered = true;
      // Mark delivery path (BFS from src to dst)
      const dPath = bfsPath(rendererSystem5.net.nodes, rendererSystem5.net.links, simState.pickedSrc, simState.pickedDst);
      if (dPath) rendererSystem5.markDeliveryPath(dPath);
    }

    const s5Txs = dEvents.filter(e => e.mode === 's5').length;
    const floodTxs = dEvents.filter(e => e.mode === 'flood').length;
    s5Html = `<span class="log-flood">${s5TxThisHop} TX</span>`;
    if (s5Txs > 0 && floodTxs > 0) {
      s5Html += ` (<span class="log-path">${s5Txs} S5-direct</span> + <span class="log-managed">${floodTxs} flood</span>)`;
    } else if (s5Txs > 0) {
      s5Html += ` (all <span class="log-path">S5-direct</span>)`;
    } else {
      s5Html += ` (all <span class="log-managed">flood</span> — no S5 nodes in this hop)`;
    }
    if (dDelivered) s5Html += `<br><span class="log-good">DST reached!</span>`;
    s5Html += `<br><span class="log-dim">Total: ${hp.s5TotalTxSoFar} TX. S5 nodes send directed (1 TX), Legacy nodes flood to all neighbors.</span>`;

  } else if (hop < hp.s5Hops && hp.s5Path) {
    // Pure System 5 directed routing — 1 TX per hop
    const from = hp.s5Path[hop];
    const to = hp.s5Path[hop + 1];
    s5TxThisHop = 1;
    hp.s5TotalTxSoFar += 1;
    rendererSystem5.markReached(from);
    rendererSystem5.addPacket(from, to, '#22d3ee', () => {
      if (simState.generation !== gen) return;
      rendererSystem5.markReached(to);
      rendererSystem5.markEdgeReached(from, to);
    });

    const fromNode = rendererSystem5.net.nodes[from];
    const toNode = rendererSystem5.net.nodes[to];
    const crossingCluster = fromNode.cluster !== toNode.cluster;

    if (hop + 1 === hp.s5Hops) {
      hp.s5Delivered = true;
      rendererSystem5.markDeliveryPath(hp.s5Path);
      s5Html = `<span class="log-good">DELIVERED!</span> Node ${from} → Node ${to}. <b>1 TX</b>.`
        + `<br><span class="log-dim">Total: ${hp.s5TotalTxSoFar} TX for ${hp.s5Hops} hops.</span>`;
    } else if (crossingCluster) {
      s5Html = `Node ${from} (C${fromNode.cluster}) → Node ${to} (C${toNode.cluster}). <b>1 TX</b>.`
        + `<br><span class="log-dim">Crossing cluster boundary via border node. Routing table knows the path.</span>`;
    } else {
      s5Html = `Node ${from} → Node ${to} (within C${fromNode.cluster}). <b>1 TX</b>.`
        + `<br><span class="log-dim">Intra-cluster hop — direct neighbor in routing table.</span>`;
    }
  } else if (hp.s5Delivered) {
    s5Html = `<span class="log-good">Already delivered</span> in ${hp.s5TotalTxSoFar} TX.`
      + `<br><span class="log-dim">${hp.rightTitle} is done — watching Managed Flooding continue...</span>`;
  } else {
    s5Html = `<span class="log-dim">No transmissions at this hop.</span>`;
  }

  // --- Managed Flooding log ---
  let mHtml = '';
  if (mHitLimit) {
    mHtml = `<span class="log-bad">HOP LIMIT REACHED (${hp.mResult.hopLimit}).</span>`
      + `<br>Packet is <b>dropped</b> — Meshtastic won't forward beyond this.`
      + `<br><span class="log-dim">Total so far: ${hp.mTotalTxSoFar} TX.</span>`;
  } else if (mTxThisHop > 0) {
    const uniqueTargets = new Set(mEvents.map(e => e.to));
    mHtml = `<span class="log-flood">${mTxThisHop} transmissions</span> to reach ${uniqueTargets.size} new nodes.`
      + `<br><span class="log-dim">Every node that heard the message rebroadcasts to ALL its neighbors.</span>`;
    if (mDeliveredThisHop) {
      mHtml += `<br><span class="log-good">DST reached!</span> But flooding continues — other nodes don't know it was delivered.`;
    }
    mHtml += `<br><span class="log-dim">Total so far: ${hp.mTotalTxSoFar} TX.</span>`;
  } else {
    mHtml = `<span class="log-dim">No transmissions at this hop level.</span>`;
  }

  // Write log entry
  const stepDiv = document.createElement('div');
  stepDiv.className = 'log-step';
  stepDiv.innerHTML = `<div class="log-header">Hop ${hop + 1}</div>
    <div class="log-columns">
      <div class="log-col left">
        <div class="log-col-title log-managed">Managed Flooding</div>${mHtml}
      </div>
      <div class="log-col right">
        <div class="log-col-title log-system5">System 5</div>${s5Html}
      </div>
    </div>
    ${(hp.mTotalTxSoFar > 0 && hp.s5TotalTxSoFar > 0) ?
      `<div class="log-comparison">After hop ${hop + 1}: Managed <span class="log-flood">${hp.mTotalTxSoFar} TX</span> vs System 5 <span class="log-path">${hp.s5TotalTxSoFar} TX</span> — <span class="log-savings">${((1 - hp.s5TotalTxSoFar / hp.mTotalTxSoFar) * 100).toFixed(0)}% saved</span></div>` : ''}`;
  log.appendChild(stepDiv);
  log.scrollTop = log.scrollHeight;

  // Update stats
  rendererManaged.stats.tx = hp.mTotalTxSoFar;
  rendererManaged.stats.lastHops = hop + 1;
  rendererManaged.stats.sent = 1;
  if (hp.mDelivered) rendererManaged.stats.delivered = 1;
  rendererManaged.stats.totalHops = hop + 1;

  rendererSystem5.stats.tx = hp.s5TotalTxSoFar;
  rendererSystem5.stats.lastHops = Math.min(hop + 1, hp.s5Hops);
  rendererSystem5.stats.sent = 1;
  if (hp.s5Delivered) rendererSystem5.stats.delivered = 1;
  rendererSystem5.stats.totalHops = Math.min(hop + 1, hp.s5Hops);

  // Check if both sides done now
  const mNowDone = (hop + 1) > hp.mMaxHop || mHitLimit;
  const s5NowDone = (hop + 1) >= hp.s5Hops;
  if (mNowDone && s5NowDone) {
    // Write summary
    setTimeout(() => {
      if (simState.generation !== gen) return;
      const sumDiv = document.createElement('div');
      sumDiv.className = 'log-step';
      sumDiv.innerHTML = `<div class="log-header">Summary</div>
        <div class="log-columns">
          <div class="log-col left">
            <div class="log-col-title log-managed">Managed Flooding</div>
            ${hp.mDelivered ? '<span class="log-good">Delivered</span>' : '<span class="log-bad">FAILED</span>'}
            after ${hp.mMaxHop + 1} hop levels, <b>${hp.mTotalTxSoFar} total TX</b>.
          </div>
          <div class="log-col right">
            <div class="log-col-title log-system5">System 5</div>
            ${hp.s5Delivered ? '<span class="log-good">Delivered</span>' : '<span class="log-bad">FAILED</span>'}
            in ${hp.s5Hops} hops, <b>${hp.s5TotalTxSoFar} total TX</b>.
          </div>
        </div>
        <div class="log-comparison">
          <b>System 5 used ${((1 - hp.s5TotalTxSoFar / Math.max(hp.mTotalTxSoFar, 1)) * 100).toFixed(0)}% fewer transmissions</b>
          (${(hp.mTotalTxSoFar / Math.max(hp.s5TotalTxSoFar, 1)).toFixed(1)}x more efficient)
          because it knew the exact path from its routing table.
        </div>`;
      log.appendChild(sumDiv);
      log.scrollTop = log.scrollHeight;
      markFinished();
    }, 500);
  }
}

function markFinished() {
  simState.running = false;
  simState.finished = true;
  const btn = document.getElementById('btn-start');
  btn.textContent = 'Restart';
  btn.classList.add('primary');
  document.getElementById('btn-step').disabled = true;
}

let lastTime = 0;
let autoDispatchTimer = 0;

function loop(timestamp) {
  const dt = Math.min((timestamp - lastTime) / 1000, 0.05);
  lastTime = timestamp;

  // Auto-advance hops when running
  if (simState.running && !simState.finished && simState.hopPlan) {
    autoDispatchTimer += dt * simState.speed;
    const interval = 1.5; // seconds between hops — wait for wave to arrive before next hop
    if (autoDispatchTimer >= interval) {
      const hp = simState.hopPlan;
      if (hp && hp.isBroadcast) advanceOneHopBroadcast();
      else advanceOneHop();
      autoDispatchTimer -= interval;
    }
  }

  rendererManaged.update(dt);
  rendererSystem5.update(dt);
  rendererManaged.draw();
  rendererSystem5.draw();

  requestAnimationFrame(loop);
}

// Boot
document.addEventListener('DOMContentLoaded', init);
