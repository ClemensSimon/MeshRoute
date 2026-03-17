// Network Formation — Step-by-step animated visualization
(function() {
  const canvas = document.getElementById('canvas-formation');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const stepNum = document.getElementById('form-step-num');
  const stepTitle = document.getElementById('form-step-title');
  const stepDesc = document.getElementById('form-step-desc');
  const logEl = document.getElementById('form-log');
  const btnNext = document.getElementById('form-btn-next');
  const btnPrev = document.getElementById('form-btn-prev');
  const btnReset = document.getElementById('form-btn-reset');

  let W = 0, H = 0;
  let nodes = [], links = [], packets = [], clusters = [];
  let currentStep = 0;
  let animating = false;
  let logEntries = [];
  let activeHopLinks = new Set();
  // central timer tracking so resetAll can kill everything
  let _timers = [];
  let _generation = 0; // incremented on reset, callbacks check this to abort

  function safeInterval(fn, ms) {
    const gen = _generation;
    const id = setInterval(() => {
      if (_generation !== gen) { clearInterval(id); return; }
      fn();
    }, ms);
    _timers.push(id);
    return id;
  }
  function safeTimeout(fn, ms) {
    const gen = _generation;
    const id = setTimeout(() => {
      if (_generation !== gen) return;
      fn();
    }, ms);
    _timers.push(id);
    return id;
  }
  function killAllTimers() {
    _timers.forEach(id => { clearInterval(id); clearTimeout(id); });
    _timers = [];
    _generation++;
  }

  const NODE_DEFS = [
    { x: 0.08, y: 0.25, name: 'A', geo: 'u0x8' },
    { x: 0.22, y: 0.15, name: 'B', geo: 'u0x8' },
    { x: 0.18, y: 0.45, name: 'C', geo: 'u0x8' },
    { x: 0.32, y: 0.35, name: 'D', geo: 'u0x8' },
    { x: 0.12, y: 0.65, name: 'E', geo: 'u0x8' },
    { x: 0.28, y: 0.60, name: 'F', geo: 'u0x8' },
    { x: 0.55, y: 0.18, name: 'G', geo: 'u0x9' },
    { x: 0.68, y: 0.12, name: 'H', geo: 'u0x9' },
    { x: 0.60, y: 0.40, name: 'I', geo: 'u0x9' },
    { x: 0.75, y: 0.35, name: 'J', geo: 'u0x9' },
    { x: 0.65, y: 0.58, name: 'K', geo: 'u0x9' },
    { x: 0.42, y: 0.75, name: 'L', geo: 'u0xd' },
    { x: 0.55, y: 0.80, name: 'M', geo: 'u0xd' },
    { x: 0.70, y: 0.78, name: 'N', geo: 'u0xd' },
    { x: 0.85, y: 0.70, name: 'O', geo: 'u0xd' },
    { x: 0.90, y: 0.50, name: 'P', geo: 'u0x9' },
  ];

  const LINK_RANGE = 0.28;
  const CLUSTER_COLORS = {
    'u0x8': { fill: 'rgba(167,139,250,0.06)', stroke: 'rgba(167,139,250,0.25)', dot: '#a78bfa', label: 'Cluster u0x8' },
    'u0x9': { fill: 'rgba(34,211,238,0.06)', stroke: 'rgba(34,211,238,0.25)', dot: '#22d3ee', label: 'Cluster u0x9' },
    'u0xd': { fill: 'rgba(251,146,60,0.06)', stroke: 'rgba(251,146,60,0.25)', dot: '#fb923c', label: 'Cluster u0xd' },
  };

  // Helper: find link index between two nodes
  function findLinkIdx(a, b) {
    return links.findIndex(l => (l.a === a && l.b === b) || (l.a === b && l.b === a));
  }

  // Helper: send a packet that visually highlights the link it uses
  function sendOnLink(from, to, color, speed, size, onArrive) {
    const li = findLinkIdx(from, to);
    if (li >= 0) activeHopLinks.add(li);
    packets.push({
      from, to, t: 0, speed: speed || 0.02, color, size: size || 4, linkIdx: li,
      onArrive: () => {
        if (li >= 0) activeHopLinks.delete(li);
        if (onArrive) onArrive();
      }
    });
  }

  // Helper: send packet chain hop-by-hop along a path
  function sendAlongPath(path, color, speed, size, onDone) {
    let hopIdx = 0;
    function nextHop() {
      if (hopIdx >= path.length - 1) { if (onDone) onDone(); return; }
      sendOnLink(path[hopIdx], path[hopIdx + 1], color, speed, size, () => {
        nodes[path[hopIdx + 1]].queue = Math.min(8, (nodes[path[hopIdx + 1]].queue || 0) + 1);
        nextHop();
      });
      hopIdx++;
    }
    nextHop();
  }

  const STEPS = [
    { title: 'Nodes Power On',
      desc: 'Individual LoRa devices boot up. Each node knows only its own ID and GPS position. No network exists yet — every node is isolated.',
      action: stepPowerOn },
    { title: 'Neighbor Discovery (Hello Beacons)',
      desc: 'Each node broadcasts a radio beacon. Nearby nodes hear it and record the sender with signal strength (RSSI). Dashed circles show each node\'s radio range.',
      action: stepHelloBeacons },
    { title: 'Link Establishment',
      desc: 'Bidirectional links are drawn between all node pairs that can hear each other. This forms the raw mesh topology. From now on, all communication follows these links.',
      action: stepLinks },
    { title: 'Geohash Calculation',
      desc: 'Each node computes its geohash from GPS. Nodes with the same prefix belong to the same geographic cluster. Colors show cluster membership.',
      action: stepGeohash },
    { title: 'Cluster Formation (OSPF Areas)',
      desc: 'Nodes self-organize into geographic clusters. Within each cluster, full topology is shared — every node knows all intra-cluster links.',
      action: stepClusters },
    { title: 'Border Node Election',
      desc: 'Nodes with links to OTHER clusters become border nodes (larger circles). They are the gateways. Each cluster has 2–3 for redundancy.',
      action: stepBorderNodes },
    { title: 'OGM Quality Measurement',
      desc: 'Originator Messages flow along links. Nodes count arrivals per neighbor — this reception rate becomes the link quality metric. Watch packets follow the links.',
      action: stepOGM },
    { title: 'Multi-Path Route Discovery',
      desc: 'Using link quality, 2–3 best paths from A to O are computed. Each route is highlighted in a different color. All hops follow existing links.',
      action: stepRoutes },
    { title: 'Load-Balanced Communication',
      desc: 'Messages from A to O are distributed across routes by weight. Each packet hops link-by-link. Watch overloaded nodes shift traffic to alternatives.',
      action: stepCommunicate },
  ];

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    W = canvas.width = r.width;
    H = canvas.height = r.height;
  }

  function log(msg, color) {
    logEntries.unshift({ msg, color: color || '#94a3b8' });
    if (logEntries.length > 20) logEntries.pop();
    logEl.innerHTML = logEntries.map(e =>
      `<div class="fl-entry"><span style="color:${e.color}">${e.msg}</span></div>`
    ).join('');
  }

  // ================================================================
  //  STEP IMPLEMENTATIONS
  // ================================================================

  function stepPowerOn(done) {
    nodes = []; links = []; packets = []; clusters = []; activeHopLinks.clear();
    let i = 0;
    const interval = safeInterval(() => {
      if (i >= NODE_DEFS.length) { clearInterval(interval); done(); return; }
      const d = NODE_DEFS[i];
      nodes.push({
        id: i, x: d.x * W, y: d.y * H, nx: d.x, ny: d.y,
        name: d.name, geo: d.geo,
        r: 7, color: '#334155', stroke: 'rgba(148,163,184,0.3)',
        labelColor: '#94a3b8', visible: true,
        opacity: 0, targetOpacity: 1,
        isBorder: false, queue: 0, battery: rand(60, 100),
        showRange: false,
      });
      log(`Node ${d.name} powered on (${d.geo})`, '#e2e8f0');
      i++;
    }, 150);
  }

  function stepHelloBeacons(done) {
    // Show radio range circles expanding, NO packets flying through air
    packets = [];
    let i = 0;
    const interval = safeInterval(() => {
      if (i >= nodes.length) { clearInterval(interval); safeTimeout(done, 800); return; }
      const n = nodes[i];
      n.showRange = true;
      n.rangeAnim = 0; // animate range circle expanding
      // count neighbors
      const neighbors = [];
      nodes.forEach(other => {
        if (other.id === n.id) return;
        if (Math.hypot(n.nx - other.nx, n.ny - other.ny) < LINK_RANGE) {
          neighbors.push(other.name);
        }
      });
      log(`${n.name} broadcasts HELLO — ${neighbors.length} neighbors respond: ${neighbors.join(', ')}`, '#fbbf24');
      i++;
    }, 250);
  }

  function stepLinks(done) {
    // hide range circles
    nodes.forEach(n => n.showRange = false);
    links = [];
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].nx - nodes[j].nx;
        const dy = nodes[i].ny - nodes[j].ny;
        const dd = Math.hypot(dx, dy);
        if (dd < LINK_RANGE) {
          links.push({
            a: i, b: j,
            color: 'rgba(148,163,184,0.3)', // visible default
            width: 1,
            opacity: 0, active: false,
          });
        }
      }
    }
    log(`${links.length} bidirectional links established`, '#4ade80');
    // animate links appearing one by one
    let shown = 0;
    const interval = safeInterval(() => {
      if (shown >= links.length) { clearInterval(interval); done(); return; }
      const l = links[shown];
      l.opacity = 1;
      log(`  ${nodes[l.a].name}↔${nodes[l.b].name}`, 'rgba(148,163,184,0.6)');
      shown++;
    }, 60);
  }

  function stepGeohash(done) {
    packets = [];
    let i = 0;
    const interval = safeInterval(() => {
      if (i >= nodes.length) { clearInterval(interval); safeTimeout(done, 400); return; }
      const n = nodes[i];
      const cc = CLUSTER_COLORS[n.geo];
      n.color = cc.dot + '60';
      n.stroke = cc.dot;
      n.labelColor = cc.dot;
      log(`${n.name}: GPS → geohash ${n.geo}`, cc.dot);
      i++;
    }, 120);
  }

  function stepClusters(done) {
    const groups = {};
    nodes.forEach(n => { if (!groups[n.geo]) groups[n.geo] = []; groups[n.geo].push(n); });
    clusters = [];
    Object.entries(groups).forEach(([geo, members]) => {
      let cx = 0, cy = 0;
      members.forEach(n => { cx += n.nx; cy += n.ny; });
      cx /= members.length; cy /= members.length;
      let maxR = 0;
      members.forEach(n => { maxR = Math.max(maxR, Math.hypot(n.nx - cx, n.ny - cy)); });
      const cc = CLUSTER_COLORS[geo];
      clusters.push({ cx, cy, r: maxR + 0.06, fill: cc.fill, stroke: cc.stroke, label: cc.label, opacity: 0 });
    });
    // color intra-cluster links stronger
    links.forEach(l => {
      if (nodes[l.a].geo === nodes[l.b].geo) {
        const cc = CLUSTER_COLORS[nodes[l.a].geo];
        l.color = cc.dot + '35';
        l.width = 1.2;
      }
    });
    let ci = 0;
    const geoKeys = Object.keys(groups);
    const interval = safeInterval(() => {
      if (ci >= clusters.length) { clearInterval(interval); done(); return; }
      clusters[ci].opacity = 1;
      log(`${clusters[ci].label} formed (${groups[geoKeys[ci]].length} nodes)`, CLUSTER_COLORS[geoKeys[ci]].dot);
      ci++;
    }, 500);
  }

  function stepBorderNodes(done) {
    const borderIds = new Set();
    links.forEach(l => {
      if (nodes[l.a].geo !== nodes[l.b].geo) {
        borderIds.add(l.a);
        borderIds.add(l.b);
        l.color = 'rgba(148,163,184,0.5)';
        l.width = 2;
      }
    });
    let shown = 0;
    const borderArr = [...borderIds];
    const interval = safeInterval(() => {
      if (shown >= borderArr.length) { clearInterval(interval); done(); return; }
      const n = nodes[borderArr[shown]];
      n.isBorder = true;
      n.r = 10;
      n.stroke = '#22d3ee';
      log(`${n.name} → border node (${n.geo})`, '#22d3ee');
      shown++;
    }, 350);
  }

  function stepOGM(done) {
    packets = [];
    const senders = [0, 6, 11, 9]; // A, G, L, J
    let si = 0;
    const interval = safeInterval(() => {
      if (si >= senders.length) { clearInterval(interval); safeTimeout(done, 1200); return; }
      const src = senders[si];
      const n = nodes[src];
      const neighbors = [];
      links.forEach(l => {
        if (l.a === src) neighbors.push(l.b);
        else if (l.b === src) neighbors.push(l.a);
      });
      neighbors.forEach((nb, i) => {
        safeTimeout(() => {
          sendOnLink(src, nb, '#fbbf24', 0.025, 3);
        }, i * 120);
      });
      log(`${n.name} → OGM to ${neighbors.length} neighbors`, '#fbbf24');
      si++;
    }, 900);
  }

  function stepRoutes(done) {
    packets = [];
    links.forEach(l => { l.active = false; });
    const paths = findKPaths(links, 0, 14, 3);
    const colors = ['#4ade80', '#2dd4bf', '#a78bfa'];
    const names = ['Primary', 'Alternate', 'Backup'];

    if (paths.length === 0) {
      log('No path found A→O!', '#f87171');
      done(); return;
    }

    // validate
    paths.forEach((path, pi) => {
      for (let i = 0; i < path.length - 1; i++) {
        if (findLinkIdx(path[i], path[i+1]) < 0) {
          log(`BUG: no link ${nodes[path[i]].name}↔${nodes[path[i+1]].name}`, '#f87171');
        }
      }
    });

    let pi = 0;
    const interval = safeInterval(() => {
      if (pi >= paths.length) { clearInterval(interval); safeTimeout(done, 500); return; }
      const path = paths[pi];
      const color = colors[pi];
      // highlight links along path
      for (let i = 0; i < path.length - 1; i++) {
        const li = findLinkIdx(path[i], path[i+1]);
        if (li >= 0) {
          links[li].active = true;
          links[li].activeColor = color;
          links[li].activeWidth = 3;
        }
      }
      const pathStr = path.map(id => nodes[id].name).join('→');
      log(`${names[pi]}: ${pathStr} (${path.length-1} hops)`, color);

      // send a tracer packet along the path
      sendAlongPath(path, color, 0.025, 4);
      pi++;
    }, 1200);
  }

  function stepCommunicate(done) {
    packets = [];
    links.forEach(l => { l.active = false; });
    activeHopLinks.clear();

    const paths = findKPaths(links, 0, 14, 3);
    if (paths.length === 0) { log('No paths!', '#f87171'); done(); return; }
    const colors = ['#4ade80', '#2dd4bf', '#a78bfa'];
    const weights = [0.5, 0.3, 0.2];

    log('Sending 6 packets A→O with load balancing...', '#22d3ee');

    let msgIdx = 0;
    const interval = safeInterval(() => {
      if (msgIdx >= 6) { clearInterval(interval); safeTimeout(done, 2000); return; }
      // weighted route selection
      const r = Math.random();
      let cumul = 0, ri = 0;
      for (let i = 0; i < weights.length && i < paths.length; i++) {
        cumul += weights[i]; if (r <= cumul) { ri = i; break; }
      }
      const path = paths[ri];
      const color = colors[ri];
      const pathStr = path.map(id => nodes[id].name).join('→');
      log(`Pkt #${msgIdx+1}: ${pathStr}`, color);

      sendAlongPath(path, color, 0.02, 5);
      msgIdx++;
    }, 1400);
  }

  // ================================================================
  //  RENDER LOOP
  // ================================================================
  function draw() {
    ctx.clearRect(0, 0, W, H);

    // background grid
    ctx.strokeStyle = 'rgba(100,116,139,0.05)'; ctx.lineWidth = 0.5;
    for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

    // clusters
    clusters.forEach(cl => {
      if (cl.opacity <= 0) return;
      ctx.globalAlpha = cl.opacity;
      ctx.beginPath(); ctx.arc(cl.cx * W, cl.cy * H, cl.r * W, 0, PI2);
      ctx.fillStyle = cl.fill; ctx.fill();
      ctx.strokeStyle = cl.stroke; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.font = '10px JetBrains Mono'; ctx.fillStyle = cl.stroke; ctx.textAlign = 'center';
      ctx.fillText(cl.label, cl.cx * W, cl.cy * H - cl.r * W - 8);
      ctx.globalAlpha = 1;
    });

    // links
    links.forEach((l, li) => {
      if (l.opacity <= 0) return;
      const a = nodes[l.a], b = nodes[l.b];
      const isHot = activeHopLinks.has(li);
      ctx.globalAlpha = l.opacity;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      if (isHot) {
        // bright glow when packet is travelling on this link
        ctx.strokeStyle = l.activeColor || '#22d3ee';
        ctx.lineWidth = 4;
        ctx.shadowColor = l.activeColor || '#22d3ee';
        ctx.shadowBlur = 8;
      } else if (l.active) {
        ctx.strokeStyle = l.activeColor || '#22d3ee';
        ctx.lineWidth = l.activeWidth || 2;
      } else {
        ctx.strokeStyle = l.color;
        ctx.lineWidth = l.width;
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
    });

    // packets
    for (let i = packets.length - 1; i >= 0; i--) {
      const p = packets[i];
      p.t += p.speed;
      if (p.t >= 1) {
        if (p.onArrive) p.onArrive();
        packets.splice(i, 1);
        continue;
      }
      const a = nodes[p.from], b = nodes[p.to];
      const x = lerp(a.x, b.x, p.t), y = lerp(a.y, b.y, p.t);
      // glow
      ctx.beginPath(); ctx.arc(x, y, p.size + 3, 0, PI2);
      ctx.fillStyle = p.color + '30';
      ctx.fill();
      // dot
      ctx.beginPath(); ctx.arc(x, y, p.size, 0, PI2);
      ctx.fillStyle = p.color;
      ctx.fill();
    }

    // nodes
    nodes.forEach(n => {
      if (!n.visible) return;
      if (n.opacity < n.targetOpacity) n.opacity = Math.min(n.targetOpacity, n.opacity + 0.05);
      ctx.globalAlpha = n.opacity;

      // radio range circle (for Hello Beacons step)
      if (n.showRange) {
        if (n.rangeAnim < 1) n.rangeAnim = Math.min(1, (n.rangeAnim || 0) + 0.03);
        ctx.beginPath();
        ctx.arc(n.x, n.y, LINK_RANGE * W * n.rangeAnim, 0, PI2);
        ctx.strokeStyle = 'rgba(251,191,36,0.15)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
        // fill
        ctx.fillStyle = 'rgba(251,191,36,0.03)';
        ctx.fill();
      }

      // border glow
      if (n.isBorder) {
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5, 0, PI2);
        ctx.fillStyle = 'rgba(34,211,238,0.1)'; ctx.fill();
      }

      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, PI2);
      ctx.fillStyle = n.color; ctx.fill();
      ctx.strokeStyle = n.stroke; ctx.lineWidth = n.isBorder ? 2.5 : 1.5; ctx.stroke();

      ctx.font = `${n.isBorder ? 11 : 10}px JetBrains Mono`;
      ctx.fillStyle = n.labelColor; ctx.textAlign = 'center';
      ctx.fillText(n.name, n.x, n.y - n.r - 6);

      // queue bar
      if (n.queue > 0) {
        const bw = 18, bh = 3;
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(n.x - bw/2, n.y + n.r + 4, bw, bh);
        ctx.fillStyle = n.queue > 4 ? '#f87171' : n.queue > 2 ? '#fbbf24' : '#4ade80';
        ctx.fillRect(n.x - bw/2, n.y + n.r + 4, bw * Math.min(1, n.queue / 8), bh);
        n.queue = Math.max(0, n.queue - 0.015);
      }
      ctx.globalAlpha = 1;
    });

    requestAnimationFrame(draw);
  }

  // ================================================================
  //  CONTROLS
  // ================================================================
  function updateUI() {
    stepNum.textContent = `Step ${currentStep} / ${STEPS.length}`;
    if (currentStep === 0) {
      stepTitle.textContent = 'Press "Next Step" to begin';
      stepDesc.textContent = 'Watch how a System 5 mesh network self-organizes from isolated nodes to a fully routed, load-balanced mesh.';
    } else {
      stepTitle.textContent = STEPS[currentStep - 1].title;
      stepDesc.textContent = STEPS[currentStep - 1].desc;
    }
    btnPrev.disabled = currentStep <= 0 || animating;
    btnNext.disabled = currentStep >= STEPS.length || animating;
    btnNext.textContent = currentStep >= STEPS.length ? 'Complete' : 'Next Step \u25B6';
  }

  function goNext() {
    if (currentStep >= STEPS.length || animating) return;
    currentStep++;
    animating = true;
    updateUI();
    STEPS[currentStep - 1].action(() => { animating = false; updateUI(); });
  }

  function goPrev() {
    if (currentStep <= 0 || animating) return;
    const target = currentStep - 1;
    resetAll();
    replayTo(target);
  }

  function resetAll() {
    killAllTimers();
    animating = false;
    currentStep = 0;
    nodes = []; links = []; packets = []; clusters = [];
    activeHopLinks.clear();
    logEntries = [];
    logEl.innerHTML = '';
    updateUI();
  }

  function replayTo(target) {
    if (target <= 0) return;
    animating = true;
    let step = 0;
    function nextReplay() {
      if (step >= target) { animating = false; currentStep = target; updateUI(); return; }
      currentStep = step + 1;
      updateUI();
      STEPS[step].action(() => { step++; nextReplay(); });
    }
    nextReplay();
  }

  btnNext.addEventListener('click', goNext);
  btnPrev.addEventListener('click', goPrev);
  btnReset.addEventListener('click', resetAll);

  resize();
  window.addEventListener('resize', resize);
  updateUI();
  draw();
})();
