// Resilience & Adaptive QoS — Interactive failure simulation
(function() {
  const canvas = document.getElementById('canvas-resilience');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const nhsCanvas = document.getElementById('canvas-nhs');
  const nhsCtx = nhsCanvas.getContext('2d');
  const nhsValueEl = document.getElementById('nhs-value');
  const nhsLevelEl = document.getElementById('nhs-level');
  const nhsCompEl = document.getElementById('nhs-components');
  const qosEl = document.getElementById('qos-classes');
  const logEl = document.getElementById('res-log');

  let W = 0, H = 0;
  let nodes = [], links = [], packets = [], clusters = [];
  let logEntries = [];
  let nhs = 1.0;
  let internetUp = true;

  // QoS priority classes
  const QOS = [
    { id: 'P0', name: 'EMERGENCY (SOS)', color: '#f87171', minNHS: 0.0 },
    { id: 'P1', name: 'Critical Warning', color: '#fb923c', minNHS: 0.1 },
    { id: 'P2', name: 'Navigation / GPS', color: '#fbbf24', minNHS: 0.3 },
    { id: 'P3', name: 'Text Messages', color: '#4ade80', minNHS: 0.5 },
    { id: 'P4', name: 'Telemetry Data', color: '#2dd4bf', minNHS: 0.6 },
    { id: 'P5', name: 'Status Reports', color: '#22d3ee', minNHS: 0.7 },
    { id: 'P6', name: 'File Transfer', color: '#a78bfa', minNHS: 0.8 },
    { id: 'P7', name: 'Firmware Update', color: '#94a3b8', minNHS: 0.9 },
  ];

  // Node definitions with cluster info
  const NODE_DEFS = [
    // Cluster A (left)
    { x:0.08, y:0.20, name:'A', geo:'c1', border:false },
    { x:0.18, y:0.35, name:'B', geo:'c1', border:false },
    { x:0.10, y:0.55, name:'C', geo:'c1', border:true },
    { x:0.25, y:0.50, name:'D', geo:'c1', border:true },
    { x:0.15, y:0.75, name:'E', geo:'c1', border:false },
    // Cluster B (center)
    { x:0.40, y:0.15, name:'F', geo:'c2', border:true },
    { x:0.55, y:0.25, name:'G', geo:'c2', border:false },
    { x:0.45, y:0.42, name:'H', geo:'c2', border:true },
    { x:0.58, y:0.48, name:'I', geo:'c2', border:true },
    { x:0.50, y:0.65, name:'J', geo:'c2', border:true },
    // Cluster C (right)
    { x:0.72, y:0.18, name:'K', geo:'c3', border:true },
    { x:0.85, y:0.30, name:'L', geo:'c3', border:false },
    { x:0.78, y:0.48, name:'M', geo:'c3', border:true },
    { x:0.90, y:0.55, name:'N', geo:'c3', border:false },
    { x:0.75, y:0.72, name:'O', geo:'c3', border:false },
    { x:0.65, y:0.82, name:'P', geo:'c3', border:true },
    // Internet gateway
    { x:0.92, y:0.10, name:'GW', geo:'gw', border:false, isGateway:true },
    // LoRa relay chain (subnet between Cluster A and C, normally dimmed)
    // These form a physical LoRa bridge when MQTT is down
    { x:0.30, y:0.88, name:'r1', geo:'relay', border:false, isRelay:true },
    { x:0.42, y:0.92, name:'r2', geo:'relay', border:false, isRelay:true },
    { x:0.55, y:0.90, name:'r3', geo:'relay', border:false, isRelay:true },
  ];

  const CLUSTER_META = {
    c1: { color:'#a78bfa', fill:'rgba(167,139,250,0.06)', stroke:'rgba(167,139,250,0.2)', label:'Cluster A' },
    c2: { color:'#22d3ee', fill:'rgba(34,211,238,0.06)', stroke:'rgba(34,211,238,0.2)', label:'Cluster B' },
    c3: { color:'#fb923c', fill:'rgba(251,146,60,0.06)', stroke:'rgba(251,146,60,0.2)', label:'Cluster C' },
  };

  const LINK_RANGE = 0.30;

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    W = canvas.width = r.width;
    H = canvas.height = r.height;
    buildNetwork();
  }

  function log(msg, color) {
    logEntries.unshift({ msg, color: color || '#94a3b8' });
    if (logEntries.length > 15) logEntries.pop();
    logEl.innerHTML = logEntries.map(e =>
      `<div class="res-log-entry"><span style="color:${e.color}">${e.msg}</span></div>`
    ).join('');
  }

  let mqttUp = true; // separate from internet — MQTT bridges between clusters

  function buildNetwork() {
    nodes = []; links = []; packets = [];
    internetUp = true;
    mqttUp = true;

    NODE_DEFS.forEach((d, i) => {
      const isRelay = d.isRelay || false;
      nodes.push({
        id: i, x: d.x * W, y: d.y * H, nx: d.x, ny: d.y,
        name: d.name, geo: d.geo,
        r: d.isGateway ? 12 : d.border ? 9 : isRelay ? 4 : 6,
        alive: true, hasGPS: true,
        isBorder: d.border || false,
        isGateway: d.isGateway || false,
        isRelay: isRelay,
        color: d.isGateway ? '#0e7490' : isRelay ? '#334155' : (CLUSTER_META[d.geo] ? CLUSTER_META[d.geo].color + '50' : '#334155'),
        stroke: d.isGateway ? '#22d3ee' : isRelay ? 'rgba(74,222,128,0.3)' : (CLUSTER_META[d.geo] ? CLUSTER_META[d.geo].color : '#64748b'),
        queue: 0,
      });
    });

    // build LoRa links (distance-based, skip relay-to-relay handled separately)
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        if (nodes[i].isRelay && nodes[j].isRelay) continue; // relay chain added below
        if (nodes[i].geo === 'relay' && nodes[j].geo === 'relay') continue;
        const dd = Math.hypot(nodes[i].nx - nodes[j].nx, nodes[i].ny - nodes[j].ny);
        if (dd < LINK_RANGE) {
          const isCross = nodes[i].geo !== nodes[j].geo && nodes[i].geo !== 'relay' && nodes[j].geo !== 'relay';
          const isInternet = nodes[i].isGateway || nodes[j].isGateway;
          const involvesRelay = nodes[i].isRelay || nodes[j].isRelay;
          links.push({
            a: i, b: j, alive: true,
            color: isInternet ? 'rgba(251,191,36,0.3)' : involvesRelay ? 'rgba(74,222,128,0.1)' : isCross ? 'rgba(148,163,184,0.35)' : (CLUSTER_META[nodes[i].geo] ? CLUSTER_META[nodes[i].geo].color + '25' : 'rgba(100,116,139,0.2)'),
            width: isInternet ? 2 : involvesRelay ? 0.8 : isCross ? 1.5 : 1,
            isInternet, isRelay: involvesRelay,
          });
        }
      }
    }

    // Relay chain: r1↔r2↔r3 (LoRa hops between relay nodes)
    const relayIds = nodes.filter(n => n.isRelay).map(n => n.id);
    for (let i = 0; i < relayIds.length - 1; i++) {
      links.push({
        a: relayIds[i], b: relayIds[i + 1], alive: true,
        color: 'rgba(74,222,128,0.1)', width: 0.8, isRelay: true,
      });
    }

    // Connect relay chain endpoints to nearest cluster border nodes
    // r1 (17) connects to E (4, cluster A bottom)
    // r3 (19) connects to P (15, cluster C bottom)
    links.push({ a: 4, b: 17, alive: true, color: 'rgba(74,222,128,0.1)', width: 0.8, isRelay: true });
    links.push({ a: 19, b: 15, alive: true, color: 'rgba(74,222,128,0.1)', width: 0.8, isRelay: true });

    // MQTT backbone links (internet-based, between cluster border nodes)
    // These represent fast MQTT bridges: D↔F, I↔K, J↔P (cross-cluster via internet)
    links.push({ a: 3, b: 5, alive: true, color: 'rgba(251,191,36,0.25)', width: 2, isMQTT: true, isInternet: false });
    links.push({ a: 8, b: 10, alive: true, color: 'rgba(251,191,36,0.25)', width: 2, isMQTT: true, isInternet: false });
    links.push({ a: 9, b: 15, alive: true, color: 'rgba(251,191,36,0.25)', width: 2, isMQTT: true, isInternet: false });

    // build cluster circles (exclude relay and gw)
    clusters = [];
    Object.entries(CLUSTER_META).forEach(([geo, meta]) => {
      const members = nodes.filter(n => n.geo === geo);
      if (members.length === 0) return;
      let cx = 0, cy = 0;
      members.forEach(n => { cx += n.nx; cy += n.ny; });
      cx /= members.length; cy /= members.length;
      let maxR = 0;
      members.forEach(n => { maxR = Math.max(maxR, Math.hypot(n.nx - cx, n.ny - cy)); });
      clusters.push({ cx, cy, r: maxR + 0.06, ...meta });
    });

    computeNHS();
    log('Network initialized — MQTT bridges + LoRa relay subnet active', '#4ade80');
  }

  // ================================================================
  //  NETWORK HEALTH SCORE — per cluster (local NHS)
  // ================================================================
  const clusterListEl = document.getElementById('nhs-cluster-list');
  let clusterNHS = {}; // { c1: 0.95, c2: 0.80, c3: 0.30 }

  function nhsLevel(v) {
    if (v >= 0.9) return { label: 'GREEN', color: '#4ade80' };
    if (v >= 0.7) return { label: 'YELLOW', color: '#fbbf24' };
    if (v >= 0.4) return { label: 'ORANGE', color: '#fb923c' };
    if (v >= 0.2) return { label: 'RED', color: '#f87171' };
    return { label: 'CRIT', color: '#f87171' };
  }

  function computeClusterNHS(geo) {
    const members = nodes.filter(n => n.geo === geo);
    const totalMembers = members.length;
    if (totalMembers === 0) return 1;

    const aliveMembers = members.filter(n => n.alive);
    const connectivity = aliveMembers.length / totalMembers;

    // intra-cluster links
    const clusterLinks = links.filter(l =>
      !l.isMQTT && !l.isInternet && !l.isRelay &&
      nodes[l.a].geo === geo && nodes[l.b].geo === geo
    );
    const aliveClusterLinks = clusterLinks.filter(l => l.alive && nodes[l.a].alive && nodes[l.b].alive);
    const linkQuality = clusterLinks.length > 0 ? aliveClusterLinks.length / clusterLinks.length : 1;

    // border nodes in this cluster
    const borders = members.filter(n => n.isBorder);
    const aliveBorders = borders.filter(n => n.alive);
    const borderHealth = borders.length > 0 ? aliveBorders.length / borders.length : 1;

    // route redundancy: sample pair within cluster
    const activeLinks = links.filter(l => l.alive && nodes[l.a].alive && nodes[l.b].alive);
    let redundancy = 1;
    if (aliveMembers.length >= 2) {
      const src = aliveMembers[0].id;
      const dst = aliveMembers[aliveMembers.length - 1].id;
      const paths = findKPaths(activeLinks, src, dst, 3);
      redundancy = Math.min(1, paths.length / 2);
    }

    // can this cluster reach the internet gateway?
    let gatewayReach = 0;
    if (internetUp) {
      const gw = nodes.find(n => n.isGateway && n.alive);
      if (gw && aliveBorders.length > 0) {
        const paths = findKPaths(activeLinks, aliveBorders[0].id, gw.id, 1);
        gatewayReach = paths.length > 0 ? 1 : 0;
      }
    }

    return Math.max(0, Math.min(1,
      0.25 * connectivity + 0.20 * redundancy + 0.20 * linkQuality + 0.20 * borderHealth + 0.15 * gatewayReach
    ));
  }

  function computeNHS() {
    // compute per-cluster NHS
    const geos = ['c1', 'c2', 'c3'];
    clusterNHS = {};
    geos.forEach(geo => { clusterNHS[geo] = computeClusterNHS(geo); });

    // display per-cluster list
    const clusterLabels = { c1: 'Cluster A', c2: 'Cluster B', c3: 'Cluster C' };
    clusterListEl.innerHTML = geos.map(geo => {
      const v = clusterNHS[geo];
      const lv = nhsLevel(v);
      const dotColor = CLUSTER_META[geo] ? CLUSTER_META[geo].color : '#64748b';
      return `<div class="nhs-cluster-row">
        <span class="nhs-cluster-dot" style="background:${dotColor}"></span>
        <span class="nhs-cluster-name">${clusterLabels[geo]}</span>
        <span class="nhs-cluster-val" style="color:${lv.color}">${v.toFixed(2)}</span>
        <span class="nhs-cluster-level" style="color:${lv.color}">${lv.label}</span>
      </div>`;
    }).join('');

    // gauge shows the WORST cluster (most critical)
    nhs = Math.min(...Object.values(clusterNHS));
    const lv = nhsLevel(nhs);
    nhsValueEl.textContent = nhs.toFixed(2);
    nhsValueEl.style.color = lv.color;
    nhsLevelEl.textContent = lv.label;
    nhsLevelEl.style.color = lv.color;

    // components show worst cluster's breakdown
    const worstGeo = geos.reduce((w, g) => clusterNHS[g] < clusterNHS[w] ? g : w, geos[0]);
    const wMembers = nodes.filter(n => n.geo === worstGeo);
    const wAlive = wMembers.filter(n => n.alive);
    const wBorders = wMembers.filter(n => n.isBorder);
    const wAliveBorders = wBorders.filter(n => n.alive);
    const wLinks = links.filter(l => !l.isMQTT && !l.isInternet && !l.isRelay && nodes[l.a].geo === worstGeo && nodes[l.b].geo === worstGeo);
    const wAliveLinks = wLinks.filter(l => l.alive && nodes[l.a].alive && nodes[l.b].alive);

    const comps = [
      { name: `${clusterLabels[worstGeo]} Connectivity`, val: wMembers.length > 0 ? wAlive.length / wMembers.length : 0 },
      { name: 'Border Nodes', val: wBorders.length > 0 ? wAliveBorders.length / wBorders.length : 1 },
      { name: 'Link Quality', val: wLinks.length > 0 ? wAliveLinks.length / wLinks.length : 1 },
      { name: 'Gateway Reachable', val: internetUp && nodes.some(n => n.isGateway && n.alive) ? 1 : 0 },
    ];
    nhsCompEl.innerHTML = comps.map(c => {
      const col = c.val > 0.7 ? '#4ade80' : c.val > 0.4 ? '#fbbf24' : '#f87171';
      return `<div class="nhs-comp-row"><span>${c.name}</span><span style="color:${col}">${(c.val*100).toFixed(0)}%</span></div>
              <div class="nhs-comp-bar"><div class="nhs-comp-fill" style="width:${c.val*100}%;background:${col}"></div></div>`;
    }).join('');

    // QoS gate uses the WORST cluster NHS (most restrictive)
    qosEl.innerHTML = QOS.map(q => {
      const allowed = nhs >= q.minNHS;
      return `<div class="qos-row ${allowed ? 'allowed' : 'blocked'}">
        <span class="qos-dot" style="background:${q.color}"></span>
        <span class="qos-label">${q.id} ${q.name}</span>
        <span class="qos-status" style="color:${allowed ? '#4ade80' : '#f87171'}">${allowed ? 'PASS' : 'BLOCKED'}</span>
      </div>`;
    }).join('');

    drawNHSGauge(nhs, lv.color);
  }

  function drawNHSGauge(value, color) {
    const c = nhsCtx;
    const w = nhsCanvas.width, h = nhsCanvas.height;
    c.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h - 8;
    const radius = 68;
    const startAngle = Math.PI;
    const endAngle = 2 * Math.PI;

    // background arc
    c.beginPath();
    c.arc(cx, cy, radius, startAngle, endAngle);
    c.strokeStyle = 'rgba(100,116,139,0.15)';
    c.lineWidth = 10;
    c.lineCap = 'round';
    c.stroke();

    // colored segments
    const segments = [
      { from: 0, to: 0.2, color: '#f87171' },
      { from: 0.2, to: 0.4, color: '#fb923c' },
      { from: 0.4, to: 0.7, color: '#fbbf24' },
      { from: 0.7, to: 0.9, color: '#4ade80' + '80' },
      { from: 0.9, to: 1.0, color: '#4ade80' },
    ];
    segments.forEach(seg => {
      c.beginPath();
      c.arc(cx, cy, radius, startAngle + seg.from * Math.PI, startAngle + seg.to * Math.PI);
      c.strokeStyle = seg.color + '30';
      c.lineWidth = 10;
      c.lineCap = 'butt';
      c.stroke();
    });

    // value arc
    c.beginPath();
    c.arc(cx, cy, radius, startAngle, startAngle + value * Math.PI);
    c.strokeStyle = color;
    c.lineWidth = 10;
    c.lineCap = 'round';
    c.stroke();

    // needle
    const needleAngle = startAngle + value * Math.PI;
    const nx = cx + Math.cos(needleAngle) * (radius - 16);
    const ny = cy + Math.sin(needleAngle) * (radius - 20);
    c.beginPath();
    c.moveTo(cx, cy);
    c.lineTo(nx, ny);
    c.strokeStyle = color;
    c.lineWidth = 2;
    c.lineCap = 'round';
    c.stroke();
    c.beginPath();
    c.arc(cx, cy, 4, 0, PI2);
    c.fillStyle = color;
    c.fill();
  }

  // ================================================================
  //  CLICK HANDLING
  // ================================================================
  canvas.addEventListener('click', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (W / rect.width);
    const my = (e.clientY - rect.top) * (H / rect.height);

    // check nodes first
    for (const n of nodes) {
      const d = Math.hypot(mx - n.x, my - n.y);
      if (d < (n.r || 6) + 8) {
        toggleNode(n);
        return;
      }
    }

    // check links
    for (const l of links) {
      const a = nodes[l.a], b = nodes[l.b];
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      if (len === 0) continue;
      // point-to-line distance
      const t = Math.max(0, Math.min(1, ((mx - a.x) * (b.x - a.x) + (my - a.y) * (b.y - a.y)) / (len * len)));
      const px = a.x + t * (b.x - a.x);
      const py = a.y + t * (b.y - a.y);
      if (Math.hypot(mx - px, my - py) < 10) {
        toggleLink(l);
        return;
      }
    }
  });

  function toggleNode(n) {
    n.alive = !n.alive;
    if (n.isGateway && !n.alive) internetUp = false;
    if (n.isGateway && n.alive) internetUp = true;
    const action = n.alive ? 'restored' : 'FAILED';
    const color = n.alive ? '#4ade80' : '#f87171';
    const type = n.isGateway ? 'Internet Gateway' : n.isBorder ? 'Border node' : 'Node';
    log(`${type} ${n.name} ${action}`, color);
    computeNHS();
    sendTrafficPulse();
  }

  function toggleLink(l) {
    l.alive = !l.alive;
    const aName = nodes[l.a].name, bName = nodes[l.b].name;
    const action = l.alive ? 'restored' : 'CUT';
    const color = l.alive ? '#4ade80' : '#f87171';
    log(`Link ${aName}↔${bName} ${action}`, color);
    computeNHS();
  }

  // ================================================================
  //  FAILURE PRESETS
  // ================================================================
  document.getElementById('fail-reset').addEventListener('click', () => {
    buildNetwork(); // full rebuild resets everything
    packets = [];
    log('All systems restored — MQTT + LoRa relay reset', '#4ade80');
  });

  document.getElementById('fail-node').addEventListener('click', () => {
    const alive = nodes.filter(n => n.alive && !n.isGateway && !n.isBorder);
    if (alive.length === 0) return;
    const victim = alive[Math.floor(Math.random() * alive.length)];
    victim.alive = false;
    log(`Node ${victim.name} FAILED (random)`, '#f87171');
    computeNHS();
    sendTrafficPulse();
  });

  document.getElementById('fail-border').addEventListener('click', () => {
    const alive = nodes.filter(n => n.alive && n.isBorder);
    if (alive.length === 0) return;
    const victim = alive[Math.floor(Math.random() * alive.length)];
    victim.alive = false;
    log(`BORDER ${victim.name} FAILED — cluster gateway down!`, '#f87171');
    computeNHS();
    sendTrafficPulse();
  });

  document.getElementById('fail-gps').addEventListener('click', () => {
    const geos = ['c1', 'c2', 'c3'];
    const geo = geos[Math.floor(Math.random() * geos.length)];
    nodes.filter(n => n.geo === geo).forEach(n => n.hasGPS = false);
    log(`GPS FAILURE in ${CLUSTER_META[geo].label} — nodes switch to neighbor-consensus`, '#fb923c');
    computeNHS();
  });

  document.getElementById('fail-internet').addEventListener('click', () => {
    const gw = nodes.find(n => n.isGateway);
    if (gw) { gw.alive = false; internetUp = false; }
    mqttUp = false;
    // kill internet links AND MQTT bridges
    links.filter(l => l.isInternet || l.isMQTT).forEach(l => l.alive = false);
    log('INTERNET + MQTT DOWN — all bridges lost!', '#f87171');
    // highlight relay chain activating
    links.filter(l => l.isRelay).forEach(l => {
      l.color = 'rgba(74,222,128,0.5)';
      l.width = 2;
    });
    nodes.filter(n => n.isRelay).forEach(n => {
      n.color = '#166534';
      n.stroke = '#4ade80';
      n.r = 6;
    });
    log('LoRa RELAY SUBNET ACTIVATED — bridging clusters via radio chain', '#4ade80');
    computeNHS();
    sendTrafficPulse();
  });

  // MQTT Bridge Down (separate from full internet down)
  const mqttBtn = document.getElementById('fail-mqtt');
  if (mqttBtn) mqttBtn.addEventListener('click', () => {
    mqttUp = false;
    links.filter(l => l.isMQTT).forEach(l => l.alive = false);
    log('MQTT BRIDGES DOWN — inter-cluster internet links cut!', '#f87171');
    log('Falling back to LoRa relay subnet...', '#fb923c');
    // activate relay chain visually
    links.filter(l => l.isRelay).forEach(l => {
      l.color = 'rgba(74,222,128,0.5)';
      l.width = 2;
    });
    nodes.filter(n => n.isRelay).forEach(n => {
      n.color = '#166534';
      n.stroke = '#4ade80';
      n.r = 6;
    });
    log('LoRa RELAY CHAIN r1→r2→r3 active — slower but working!', '#4ade80');
    computeNHS();
    sendTrafficPulse();
  });

  document.getElementById('fail-cascade').addEventListener('click', () => {
    log('CASCADE FAILURE — 3 nodes going down...', '#f87171');
    const alive = nodes.filter(n => n.alive && !n.isGateway);
    for (let i = 0; i < 3 && alive.length > i; i++) {
      const idx = Math.floor(Math.random() * alive.length);
      alive[idx].alive = false;
      log(`  ${alive[idx].name} DOWN`, '#f87171');
      alive.splice(idx, 1);
    }
    computeNHS();
    sendTrafficPulse();
  });

  // ================================================================
  //  TRAFFIC PULSE — show rerouting after failure
  // ================================================================
  function sendTrafficPulse() {
    packets = [];
    // try to find a path from node 0 to node 14
    const activeLinks = links.filter(l => l.alive && nodes[l.a].alive && nodes[l.b].alive);
    // find alive start/end
    const src = nodes.find(n => n.alive && n.geo === 'c1');
    const dst = nodes.find(n => n.alive && n.geo === 'c3');
    if (!src || !dst) {
      log('No path possible — network fragmented', '#f87171');
      return;
    }
    const paths = findKPaths(activeLinks, src.id, dst.id, 2);
    if (paths.length === 0) {
      log(`No route ${src.name}→${dst.name} — isolated!`, '#f87171');
      return;
    }
    const path = paths[0];
    const pathStr = path.map(id => nodes[id].name).join('→');
    log(`Rerouted: ${pathStr} (${path.length-1} hops)`, '#4ade80');
    // animate
    for (let i = 0; i < path.length - 1; i++) {
      ((from, to, delay) => {
        setTimeout(() => {
          const li = links.findIndex(l =>
            l.alive && ((l.a === from && l.b === to) || (l.a === to && l.b === from))
          );
          packets.push({ from, to, t: 0, speed: 0.018, color: '#4ade80', size: 5, linkIdx: li });
        }, delay);
      })(path[i], path[i+1], i * 350);
    }
  }

  // ================================================================
  //  RENDER
  // ================================================================
  function draw() {
    ctx.clearRect(0, 0, W, H);

    // grid
    ctx.strokeStyle = 'rgba(100,116,139,0.04)'; ctx.lineWidth = 0.5;
    for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

    // clusters
    clusters.forEach(cl => {
      ctx.beginPath(); ctx.arc(cl.cx * W, cl.cy * H, cl.r * W, 0, PI2);
      ctx.fillStyle = cl.fill; ctx.fill();
      ctx.strokeStyle = cl.stroke; ctx.lineWidth = 1; ctx.stroke();
      ctx.font = '9px JetBrains Mono'; ctx.fillStyle = cl.stroke; ctx.textAlign = 'center';
      ctx.fillText(cl.label, cl.cx * W, cl.cy * H - cl.r * W - 6);
    });

    // links
    links.forEach(l => {
      const a = nodes[l.a], b = nodes[l.b];
      if (!l.alive || !a.alive || !b.alive) {
        // dead link — red dashed
        if ((!l.alive || !a.alive || !b.alive) && a.alive !== false && b.alive !== false && !l.alive) {
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
          ctx.strokeStyle = 'rgba(248,113,113,0.15)';
          ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
          ctx.stroke(); ctx.setLineDash([]);
        }
        return;
      }
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = l.color;
      ctx.lineWidth = l.width;
      if (l.isInternet || l.isMQTT) { ctx.setLineDash([6, 4]); }
      else if (l.isRelay) { ctx.setLineDash([3, 3]); }
      ctx.stroke(); ctx.setLineDash([]);
    });

    // packets
    for (let i = packets.length - 1; i >= 0; i--) {
      const p = packets[i];
      p.t += p.speed;
      if (p.t >= 1) { packets.splice(i, 1); continue; }
      const a = nodes[p.from], b = nodes[p.to];
      const x = lerp(a.x, b.x, p.t), y = lerp(a.y, b.y, p.t);
      // highlight link
      if (p.linkIdx >= 0) {
        const l = links[p.linkIdx];
        const la = nodes[l.a], lb = nodes[l.b];
        ctx.beginPath(); ctx.moveTo(la.x, la.y); ctx.lineTo(lb.x, lb.y);
        ctx.strokeStyle = p.color + '60'; ctx.lineWidth = 4;
        ctx.shadowColor = p.color; ctx.shadowBlur = 6; ctx.stroke(); ctx.shadowBlur = 0;
      }
      ctx.beginPath(); ctx.arc(x, y, p.size + 2, 0, PI2);
      ctx.fillStyle = p.color + '25'; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, p.size, 0, PI2);
      ctx.fillStyle = p.color; ctx.fill();
    }

    // nodes
    nodes.forEach(n => {
      if (!n.alive) {
        // dead node — X marker
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, PI2);
        ctx.fillStyle = 'rgba(248,113,113,0.1)'; ctx.fill();
        ctx.strokeStyle = 'rgba(248,113,113,0.3)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.strokeStyle = '#f87171'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(n.x - 4, n.y - 4); ctx.lineTo(n.x + 4, n.y + 4); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(n.x + 4, n.y - 4); ctx.lineTo(n.x - 4, n.y + 4); ctx.stroke();
        ctx.font = '9px JetBrains Mono'; ctx.fillStyle = 'rgba(248,113,113,0.4)';
        ctx.textAlign = 'center'; ctx.fillText(n.name, n.x, n.y - n.r - 5);
        return;
      }

      // GPS warning
      if (!n.hasGPS && !n.isGateway) {
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 6, 0, PI2);
        ctx.strokeStyle = 'rgba(251,146,60,0.3)'; ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);
      }

      // border glow
      if (n.isBorder) {
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5, 0, PI2);
        ctx.fillStyle = 'rgba(34,211,238,0.08)'; ctx.fill();
      }
      // gateway glow
      if (n.isGateway) {
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 6, 0, PI2);
        ctx.fillStyle = internetUp ? 'rgba(74,222,128,0.08)' : 'rgba(248,113,113,0.08)'; ctx.fill();
      }

      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, PI2);
      ctx.fillStyle = n.color; ctx.fill();
      ctx.strokeStyle = n.stroke; ctx.lineWidth = n.isBorder ? 2.5 : n.isGateway ? 2.5 : 1.5;
      ctx.stroke();

      // label
      ctx.font = `${n.isGateway ? 10 : n.isBorder ? 10 : 9}px JetBrains Mono`;
      ctx.fillStyle = n.isGateway ? '#fbbf24' : n.isBorder ? '#22d3ee' : '#94a3b8';
      ctx.textAlign = 'center';
      ctx.fillText(n.name, n.x, n.y - n.r - 5);

      // GPS-off icon
      if (!n.hasGPS && !n.isGateway) {
        ctx.font = '7px JetBrains Mono';
        ctx.fillStyle = '#fb923c';
        ctx.fillText('!GPS', n.x, n.y + n.r + 10);
      }
    });

    requestAnimationFrame(draw);
  }

  // Start
  resize();
  window.addEventListener('resize', resize);
  // start when visible
  const obs = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) { draw(); obs.disconnect(); }
  }, { threshold: 0.1 });
  obs.observe(canvas);
})();
