// Scale scenario simulations: Local, Europe, Global

// ============================================================
//  SCENARIO 1: LOCAL — Munich neighborhood
// ============================================================
const localSim = new MapSim('canvas-local', 'panel-local-content', {
  drawMap(sim, ctx) {
    ctx.save();
    ctx.strokeStyle = 'rgba(100,116,139,0.07)'; ctx.lineWidth = 1;
    for (let y = 0.15; y < 0.95; y += 0.12) {
      ctx.beginPath(); ctx.moveTo(sim.W*0.03, y*sim.H); ctx.lineTo(sim.W*0.97, y*sim.H); ctx.stroke();
    }
    for (let x = 0.1; x < 0.95; x += 0.1) {
      ctx.beginPath(); ctx.moveTo(x*sim.W, sim.H*0.05); ctx.lineTo(x*sim.W, sim.H*0.95); ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(34,211,238,0.04)'; ctx.lineWidth = 18;
    ctx.beginPath(); ctx.moveTo(sim.W*0.7, 0); ctx.quadraticCurveTo(sim.W*0.65, sim.H*0.5, sim.W*0.75, sim.H); ctx.stroke();
    ctx.fillStyle = 'rgba(74,222,128,0.03)';
    ctx.beginPath(); ctx.arc(sim.W*0.25, sim.H*0.3, 50, 0, PI2); ctx.fill();
    ctx.beginPath(); ctx.arc(sim.W*0.8, sim.H*0.6, 40, 0, PI2); ctx.fill();
    ctx.font = '11px JetBrains Mono'; ctx.fillStyle = 'rgba(100,116,139,0.15)'; ctx.textAlign = 'center';
    ctx.fillText('SCHWABING', sim.W*0.3, sim.H*0.08);
    ctx.fillText('MAXVORSTADT', sim.W*0.5, sim.H*0.55);
    ctx.fillText('BOGENHAUSEN', sim.W*0.8, sim.H*0.08);
    ctx.fillText('GEOHASH: u0x8m', sim.W*0.5, sim.H*0.97);
    ctx.restore();
  },
  setup(sim) {
    sim._localTimer = 0; sim._msgCount = 0;
    const positions = [
      [0.12, 0.22, 'N01 (Cafe)'],  [0.28, 0.14, 'N02 (Roof)'],
      [0.45, 0.18, 'N03 (Office)'],[0.62, 0.12, 'N04 (Tower)'],
      [0.82, 0.20, 'N05 (Uni)'],   [0.08, 0.52, 'N06 (Park)'],
      [0.30, 0.48, 'N07 (Home)'],  [0.50, 0.42, 'N08 (Shop)'],
      [0.72, 0.50, 'N09 (Bridge)'],[0.18, 0.78, 'N10 (School)'],
      [0.48, 0.75, 'N11 (Gym)'],   [0.78, 0.80, 'N12 (Church)'],
    ];
    positions.forEach((p, i) => sim.addNode(p[0], p[1], {
      label: p[2], r: 7, color: '#1e3a5f', stroke: 'var(--cyan)',
      showBattery: true, battery: rand(30, 100)
    }));
    for (let i = 0; i < sim.nodes.length; i++)
      for (let j = i+1; j < sim.nodes.length; j++)
        if (dist(sim.nodes[i], sim.nodes[j]) < sim.W * 0.35)
          sim.addLink(i, j);
    // pre-compute 3 disjoint paths from node 0 (Cafe) to node 11 (Church)
    sim._routes = findKPaths(sim.links, 0, 11, 3);
    sim._routeNames = ['Primary', 'Alternate', 'Backup'];
    sim._routeColors = ['#4ade80', '#2dd4bf', '#a78bfa'];
  },
  tick(sim) {
    sim._localTimer += 0.016;
    sim.nodes.forEach(n => { n.queue = Math.max(0, n.queue - 0.01); n.active = false; });
    sim.links.forEach(l => l.active = false);

    // periodic OGM beacons
    if (Math.floor(sim.time * 2) % 16 === 0 && sim.time - (sim._lastOgm || 0) > 3) {
      sim._lastOgm = sim.time;
      const sender = Math.floor(rand(0, sim.nodes.length));
      sim.nodes[sender].active = true;
      sim.nodes[sender].glowColor = 'rgba(251,191,36,0.15)';
      const neighbors = sim.links.filter(l => l.a === sender || l.b === sender).map(l => l.a === sender ? l.b : l.a);
      neighbors.forEach((nb, i) => {
        setTimeout(() => {
          sim.sendPacket(sender, nb, '#fbbf24', 0.02, 3, () => { sim.nodes[nb].rxCount++; });
        }, i * 60);
      });
      sim.log(`N${(sender+1).toString().padStart(2,'0')} → OGM beacon to ${neighbors.length} neighbors`, '#fbbf24');
    }

    // data messages
    if (sim._localTimer > 2.5) {
      sim._localTimer = 0;
      sim._msgCount++;

      // use BFS-computed routes (guaranteed to follow links)
      const routes = sim._routes;
      if (routes.length === 0) return;

      const routeWeight = (path) => {
        let maxQ = 0;
        path.forEach(id => maxQ = Math.max(maxQ, sim.nodes[id].queue));
        return Math.max(0.1, 1 - maxQ/8);
      };
      const weights = routes.map(routeWeight);
      const total = weights.reduce((s,w) => s+w, 0);
      const shares = weights.map(w => w/total);

      // pick route weighted by shares
      const rnd = Math.random();
      let cumul = 0, chosenIdx = 0;
      for (let i = 0; i < shares.length; i++) {
        cumul += shares[i];
        if (rnd <= cumul) { chosenIdx = i; break; }
      }
      const chosenRoute = routes[chosenIdx];
      const routeName = sim._routeNames[chosenIdx] || `Route ${chosenIdx+1}`;
      const routeColor = sim._routeColors[chosenIdx] || '#4ade80';

      sim.log(`MSG #${sim._msgCount}: N01→N12 via ${routeName} (${chosenRoute.length-1} hops)`, routeColor);

      for (let i = 0; i < chosenRoute.length - 1; i++) {
        const a = chosenRoute[i], b = chosenRoute[i+1];
        const li = sim.links.findIndex(l => (l.a===a && l.b===b) || (l.a===b && l.b===a));
        if (li >= 0) { sim.links[li].active = true; sim.links[li].activeColor = routeColor; }
      }

      let delay = 0;
      for (let i = 0; i < chosenRoute.length - 1; i++) {
        ((from, to, d) => {
          setTimeout(() => {
            sim.nodes[from].active = true;
            sim.nodes[from].glowColor = routeColor + '20';
            sim.sendPacket(from, to, routeColor, 0.02, 5, (arrived) => {
              const n = sim.nodes[arrived];
              n.queue = Math.min(8, n.queue + 1.5);
              n.fwdCount++;
              n.battery = Math.max(5, n.battery - 0.3);
              n.active = true; n.glowColor = routeColor + '20';
              if (n.queue > 4) sim.log(`N${(arrived+1).toString().padStart(2,'0')} back-pressure! queue=${n.queue.toFixed(1)}`, '#fb923c');
            });
          }, d);
        })(chosenRoute[i], chosenRoute[i+1], delay);
        delay += 400;
      }
      setTimeout(() => {
        const wStr = shares.map((s, i) => `${sim._routeNames[i] || 'R'+(i+1)}=${(s*100).toFixed(0)}%`).join(' ');
        sim.log(`Weights: ${wStr}`, 'var(--text-muted)');
      }, 200);
    }
  }
});

// ============================================================
//  SCENARIO 2: EUROPE
// ============================================================
const europeSim = new MapSim('canvas-europe', 'panel-europe-content', {
  drawMap(sim, ctx) {
    ctx.save();
    ctx.strokeStyle = 'rgba(100,116,139,0.12)'; ctx.lineWidth = 1.5;
    ctx.beginPath();
    const pts = [
      [0.35,0.05],[0.42,0.08],[0.48,0.03],[0.55,0.06],[0.65,0.04],
      [0.72,0.08],[0.78,0.06],[0.85,0.12],[0.88,0.20],[0.92,0.28],
      [0.90,0.38],[0.88,0.45],[0.92,0.52],[0.88,0.58],[0.84,0.52],
      [0.80,0.55],[0.82,0.62],[0.78,0.68],[0.72,0.72],[0.68,0.78],
      [0.60,0.80],[0.55,0.85],[0.48,0.88],[0.42,0.85],[0.38,0.80],
      [0.35,0.75],[0.30,0.78],[0.25,0.82],[0.20,0.78],[0.18,0.70],
      [0.15,0.62],[0.12,0.55],[0.10,0.45],[0.12,0.35],[0.15,0.25],
      [0.18,0.18],[0.22,0.12],[0.28,0.08],[0.35,0.05]
    ];
    ctx.moveTo(pts[0][0]*sim.W, pts[0][1]*sim.H);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0]*sim.W, pts[i][1]*sim.H);
    ctx.closePath();
    ctx.fillStyle = 'rgba(100,116,139,0.03)'; ctx.fill(); ctx.stroke();
    ctx.font = '9px JetBrains Mono'; ctx.fillStyle = 'rgba(100,116,139,0.12)'; ctx.textAlign = 'center';
    ctx.fillText('NORWAY', sim.W*0.42, sim.H*0.12);
    ctx.fillText('UK', sim.W*0.18, sim.H*0.38);
    ctx.fillText('FRANCE', sim.W*0.30, sim.H*0.58);
    ctx.fillText('SPAIN', sim.W*0.25, sim.H*0.78);
    ctx.fillText('GERMANY', sim.W*0.52, sim.H*0.38);
    ctx.fillText('POLAND', sim.W*0.65, sim.H*0.32);
    ctx.fillText('ITALY', sim.W*0.55, sim.H*0.68);
    ctx.fillText('GREECE', sim.W*0.72, sim.H*0.72);
    ctx.restore();
  },
  drawOverlay(sim, ctx) {
    sim._clusters.forEach(cl => {
      ctx.beginPath(); ctx.arc(cl.x * sim.W, cl.y * sim.H, cl.r * sim.W, 0, PI2);
      ctx.fillStyle = cl.fill; ctx.fill();
      ctx.strokeStyle = cl.stroke; ctx.lineWidth = 1; ctx.stroke();
    });
  },
  setup(sim) {
    sim._euroTimer = 0; sim._euroPhase = 0;
    sim._clusters = [
      { x:0.20, y:0.35, r:0.045, name:'London', fill:'rgba(167,139,250,0.08)', stroke:'rgba(167,139,250,0.25)' },
      { x:0.35, y:0.42, r:0.04, name:'Paris', fill:'rgba(167,139,250,0.08)', stroke:'rgba(167,139,250,0.25)' },
      { x:0.30, y:0.72, r:0.035, name:'Madrid', fill:'rgba(167,139,250,0.08)', stroke:'rgba(167,139,250,0.25)' },
      { x:0.50, y:0.32, r:0.04, name:'Berlin', fill:'rgba(34,211,238,0.08)', stroke:'rgba(34,211,238,0.25)' },
      { x:0.52, y:0.48, r:0.045, name:'Munich', fill:'rgba(34,211,238,0.1)', stroke:'rgba(34,211,238,0.3)' },
      { x:0.55, y:0.65, r:0.035, name:'Rome', fill:'rgba(251,146,60,0.08)', stroke:'rgba(251,146,60,0.25)' },
      { x:0.68, y:0.38, r:0.035, name:'Warsaw', fill:'rgba(74,222,128,0.08)', stroke:'rgba(74,222,128,0.25)' },
      { x:0.72, y:0.60, r:0.035, name:'Athens', fill:'rgba(251,146,60,0.08)', stroke:'rgba(251,146,60,0.25)' },
    ];
    // 3 nodes per cluster: gateway (index c*3) + 2 sub-nodes
    sim._clusters.forEach((cl) => {
      sim.addNode(cl.x, cl.y, {
        label: cl.name, r: 9, color: '#0e7490', stroke: '#22d3ee',
        labelColor: '#e2e8f0', fontSize: 10, isGateway: true, showBattery: false
      });
      sim.addNode(cl.x + rand(-0.03, 0.03), cl.y + rand(-0.03, 0.03), {
        r: 4, color: '#1a2236', stroke: cl.stroke.replace(/[\d.]+\)/, '0.5)'), showBattery: false
      });
      sim.addNode(cl.x + rand(-0.03, 0.03), cl.y + rand(-0.03, 0.03), {
        r: 4, color: '#1a2236', stroke: cl.stroke.replace(/[\d.]+\)/, '0.5)'), showBattery: false
      });
    });
    // intra-cluster links
    for (let c = 0; c < 8; c++) {
      const base = c * 3;
      sim.addLink(base, base+1); sim.addLink(base, base+2); sim.addLink(base+1, base+2);
    }
    // inter-cluster (gateway-to-gateway) MQTT bridges
    // Gateways: London=0, Paris=3, Madrid=6, Berlin=9, Munich=12, Rome=15, Warsaw=18, Athens=21
    const interLinks = [[0,3],[0,9],[3,9],[3,12],[9,12],[12,15],[15,21],[12,18],[9,18],[18,21],[0,6],[6,3],[6,12]];
    interLinks.forEach(([a,b]) => sim.addLink(a, b, { dashed: true, color: 'rgba(251,146,60,0.12)', width: 1.2 }));
  },
  tick(sim) {
    sim._euroTimer += 0.016;
    sim.nodes.forEach(n => { n.active = false; n.queue = Math.max(0, n.queue - 0.005); });
    sim.links.forEach(l => l.active = false);

    if (sim._euroTimer > 4) {
      sim._euroTimer = 0;
      sim._euroPhase = (sim._euroPhase + 1) % 3;

      if (sim._euroPhase === 0) {
        // Munich(12) → London(0): via Berlin(9)
        sim.log('━━━ Munich → London ━━━', 'var(--cyan)');
        sim.log('Phase 1: DNS lookup...', '#fbbf24');
        const dnsPath = [12, 9, 0];
        let d = 0;
        for (let i = 0; i < dnsPath.length-1; i++) {
          ((f,t,dl) => setTimeout(() => {
            sim.sendPacket(f, t, '#fbbf24', 0.012, 3, (a) => {
              sim.nodes[a].active = true; sim.nodes[a].glowColor = 'rgba(251,191,36,0.15)';
              sim.log(`  DNS: ${sim.nodes[f].label} → ${sim.nodes[a].label}`, '#fbbf24');
            });
          }, dl))(dnsPath[i], dnsPath[i+1], d);
          d += 800;
        }
        setTimeout(() => {
          sim.log('Phase 2: Sending data...', '#4ade80');
          const dataPath = [12, 9, 0];
          let dd = 0;
          for (let i = 0; i < dataPath.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              const li = sim.links.findIndex(l => (l.a===f&&l.b===t)||(l.a===t&&l.b===f));
              if(li>=0){sim.links[li].active=true;sim.links[li].activeColor='rgba(74,222,128,0.6)';sim.links[li].activeWidth=3;}
              sim.sendPacket(f, t, '#4ade80', 0.01, 6, (a) => {
                sim.nodes[a].active = true; sim.nodes[a].queue += 2;
                sim.log(`  DATA → ${sim.nodes[a].label} [queue:${sim.nodes[a].queue.toFixed(0)}]`, '#4ade80');
              });
            }, dl))(dataPath[i], dataPath[i+1], dd);
            dd += 1000;
          }
        }, d + 500);

      } else if (sim._euroPhase === 1) {
        // Madrid(6) → Warsaw(18): via Paris(3) → Berlin(9)
        sim.log('━━━ Madrid → Warsaw ━━━', 'var(--cyan)');
        sim.log('Phase 1: Scoped lookup...', '#fbbf24');
        const dnsPath = [6, 3, 9, 18];
        let d = 0;
        for (let i = 0; i < dnsPath.length-1; i++) {
          ((f,t,dl) => setTimeout(() => {
            sim.sendPacket(f, t, '#fbbf24', 0.012, 3, () => {
              sim.log(`  DNS: ${sim.nodes[f].label||'GW'} → ${sim.nodes[t].label||'GW'}`, '#fbbf24');
            });
          }, dl))(dnsPath[i], dnsPath[i+1], d);
          d += 800;
        }
        setTimeout(() => {
          sim.log('Phase 2: Dual-path...', '#4ade80');
          // Primary: 6→3→9→18
          const primary = [6, 3, 9, 18];
          let dd = 0;
          for (let i = 0; i < primary.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              const li = sim.links.findIndex(l=>(l.a===f&&l.b===t)||(l.a===t&&l.b===f));
              if(li>=0){sim.links[li].active=true;sim.links[li].activeColor='rgba(74,222,128,0.6)';sim.links[li].activeWidth=3;}
              sim.sendPacket(f,t,'#4ade80',0.01,5,a=>{sim.nodes[a].queue+=1.5;});
            },dl))(primary[i],primary[i+1],dd);
            dd+=900;
          }
          // Backup: 6→12→9→18 (via Munich)
          const backup = [6, 12, 9, 18];
          dd = 400;
          for (let i = 0; i < backup.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              sim.sendPacket(f,t,'rgba(45,212,191,0.6)',0.008,3,()=>{});
            },dl))(backup[i],backup[i+1],dd);
            dd+=900;
          }
          sim.log('  Backup: Madrid→Munich→Berlin→Warsaw', '#2dd4bf');
        }, d + 500);

      } else {
        // OGM round
        sim.log('━━━ OGM exchange ━━━', '#fbbf24');
        [0,3,9,12,18].forEach((gw,i) => {
          setTimeout(() => {
            const neighbors = sim.links.filter(l => l.a===gw || l.b===gw).map(l => l.a===gw ? l.b : l.a).slice(0,3);
            neighbors.forEach(nb => sim.sendPacket(gw, nb, 'rgba(251,191,36,0.4)', 0.015, 2, ()=>{}));
            sim.log(`  ${sim.nodes[gw].label} OGM → ${neighbors.length} peers`, 'rgba(251,191,36,0.7)');
          }, i * 500);
        });
      }
    }
  }
});

// ============================================================
//  SCENARIO 3: GLOBAL
// ============================================================
const globalSim = new MapSim('canvas-global', 'panel-global-content', {
  drawMap(sim, ctx) {
    ctx.save();
    ctx.strokeStyle = 'rgba(100,116,139,0.1)'; ctx.lineWidth = 1;
    ctx.fillStyle = 'rgba(100,116,139,0.025)';
    // Simplified continent outlines
    const continents = {
      na: [[0.05,0.18],[0.08,0.12],[0.14,0.08],[0.22,0.10],[0.26,0.15],[0.25,0.22],[0.22,0.30],[0.18,0.38],[0.20,0.45],[0.18,0.50],[0.12,0.48],[0.08,0.40],[0.05,0.30]],
      sa: [[0.18,0.55],[0.22,0.52],[0.25,0.58],[0.26,0.68],[0.24,0.78],[0.22,0.85],[0.20,0.90],[0.18,0.88],[0.16,0.78],[0.15,0.68],[0.16,0.58]],
      eu: [[0.38,0.10],[0.42,0.08],[0.48,0.12],[0.52,0.10],[0.50,0.18],[0.48,0.25],[0.52,0.32],[0.48,0.38],[0.45,0.35],[0.42,0.38],[0.44,0.48],[0.48,0.55],[0.46,0.65],[0.44,0.75],[0.40,0.78],[0.38,0.70],[0.36,0.60],[0.35,0.50],[0.36,0.40],[0.38,0.30],[0.36,0.20]],
      as: [[0.52,0.10],[0.58,0.08],[0.65,0.10],[0.72,0.12],[0.80,0.15],[0.85,0.20],[0.82,0.30],[0.80,0.38],[0.78,0.45],[0.72,0.48],[0.68,0.42],[0.62,0.45],[0.58,0.40],[0.55,0.35],[0.52,0.28],[0.50,0.20]],
      au: [[0.78,0.62],[0.85,0.60],[0.90,0.65],[0.92,0.72],[0.88,0.78],[0.82,0.80],[0.78,0.75],[0.76,0.68]]
    };
    Object.values(continents).forEach(pts => {
      ctx.beginPath();
      ctx.moveTo(pts[0][0]*sim.W, pts[0][1]*sim.H);
      pts.forEach(p => ctx.lineTo(p[0]*sim.W, p[1]*sim.H));
      ctx.closePath(); ctx.fill(); ctx.stroke();
    });
    ctx.font = '10px JetBrains Mono'; ctx.fillStyle = 'rgba(100,116,139,0.12)'; ctx.textAlign = 'center';
    ctx.fillText('NORTH AMERICA', sim.W*0.15, sim.H*0.25);
    ctx.fillText('SOUTH AMERICA', sim.W*0.21, sim.H*0.72);
    ctx.fillText('EUROPE', sim.W*0.44, sim.H*0.22);
    ctx.fillText('AFRICA', sim.W*0.43, sim.H*0.55);
    ctx.fillText('ASIA', sim.W*0.68, sim.H*0.25);
    ctx.fillText('OCEANIA', sim.W*0.85, sim.H*0.72);
    ctx.restore();
  },
  drawOverlay(sim, ctx) {
    sim._superClusters.forEach(sc => {
      ctx.beginPath(); ctx.arc(sc.x*sim.W, sc.y*sim.H, sc.r*sim.W, 0, PI2);
      ctx.fillStyle = sc.fill; ctx.fill();
      ctx.strokeStyle = sc.stroke; ctx.lineWidth = 1.5; ctx.stroke();
    });
  },
  setup(sim) {
    sim._globalTimer = 0; sim._globalPhase = 0;
    sim._superClusters = [
      { x:0.15, y:0.30, r:0.06, name:'NA-West', fill:'rgba(251,146,60,0.06)', stroke:'rgba(251,146,60,0.2)' },
      { x:0.22, y:0.42, r:0.05, name:'NA-East', fill:'rgba(251,146,60,0.06)', stroke:'rgba(251,146,60,0.2)' },
      { x:0.21, y:0.68, r:0.04, name:'SA-North', fill:'rgba(74,222,128,0.06)', stroke:'rgba(74,222,128,0.2)' },
      { x:0.44, y:0.25, r:0.05, name:'EU-West', fill:'rgba(34,211,238,0.08)', stroke:'rgba(34,211,238,0.3)' },
      { x:0.50, y:0.18, r:0.04, name:'EU-East', fill:'rgba(34,211,238,0.06)', stroke:'rgba(34,211,238,0.2)' },
      { x:0.44, y:0.50, r:0.04, name:'Africa', fill:'rgba(167,139,250,0.06)', stroke:'rgba(167,139,250,0.2)' },
      { x:0.65, y:0.22, r:0.05, name:'Asia-C', fill:'rgba(248,113,113,0.06)', stroke:'rgba(248,113,113,0.2)' },
      { x:0.78, y:0.35, r:0.05, name:'Asia-E', fill:'rgba(248,113,113,0.06)', stroke:'rgba(248,113,113,0.2)' },
      { x:0.85, y:0.70, r:0.04, name:'Oceania', fill:'rgba(251,191,36,0.06)', stroke:'rgba(251,191,36,0.2)' },
    ];
    // gateway (even index) + sub-node (odd index) per cluster
    sim._superClusters.forEach((sc) => {
      sim.addNode(sc.x, sc.y, {
        label: sc.name, r: 10, color: '#0e4a3a', stroke: '#22d3ee',
        labelColor: '#cbd5e1', fontSize: 9, isGateway: true, showBattery: false
      });
      sim.addNode(sc.x + rand(-0.025,0.025), sc.y + rand(-0.025,0.025), {
        r: 4, color: '#1a2236', stroke: sc.stroke.replace(/[\d.]+\)$/,'0.4)'), showBattery: false
      });
    });
    // intra-cluster links (gateway to sub-node)
    for (let i = 0; i < 9; i++) sim.addLink(i*2, i*2+1);
    // Backbone: GW indices = 0,2,4,6,8,10,12,14,16
    // NA-W=0, NA-E=2, SA=4, EU-W=6, EU-E=8, Africa=10, Asia-C=12, Asia-E=14, Oceania=16
    const backbone = [[0,2],[2,4],[0,6],[6,8],[8,10],[6,10],[8,12],[12,14],[14,16],[12,16],[2,6],[4,10],[10,14]];
    backbone.forEach(([a,b]) => sim.addLink(a, b, { dashed: true, color: 'rgba(255,255,255,0.06)', width: 1 }));
  },
  tick(sim) {
    sim._globalTimer += 0.016;
    sim.nodes.forEach(n => { n.active = false; n.queue = Math.max(0, n.queue - 0.003); });
    sim.links.forEach(l => l.active = false);

    if (sim._globalTimer > 5) {
      sim._globalTimer = 0;
      sim._globalPhase = (sim._globalPhase + 1) % 3;

      if (sim._globalPhase === 0) {
        // EU-West(6) → Asia-East(14)
        sim.log('━━━ EU-West → Asia-East ━━━', 'var(--cyan)');
        sim.log('Phase 1: DNS cascade...', '#fbbf24');
        const dnsPath = [6, 8, 12, 14];
        let d = 0;
        const labels = ['EU-East','Asia-C','Asia-E'];
        for (let i = 0; i < dnsPath.length-1; i++) {
          ((f,t,dl,idx) => setTimeout(() => {
            sim.sendPacket(f,t,'#fbbf24',0.008,3,(a)=>{
              sim.nodes[a].active=true; sim.nodes[a].glowColor='rgba(251,191,36,0.15)';
              sim.log(`  DNS → ${labels[idx]}: ${idx<2?'forward':'FOUND!'}`, '#fbbf24');
            });
          },dl))(dnsPath[i],dnsPath[i+1],d,i);
          d += 1200;
        }
        setTimeout(() => {
          sim.log('Phase 2: Dual-path...', '#4ade80');
          const primary = [6, 8, 12, 14];
          let dd = 0;
          for (let i = 0; i < primary.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              const li = sim.links.findIndex(l=>(l.a===f&&l.b===t)||(l.a===t&&l.b===f));
              if(li>=0){sim.links[li].active=true;sim.links[li].activeColor='rgba(74,222,128,0.5)';sim.links[li].activeWidth=3;}
              sim.sendPacket(f,t,'#4ade80',0.006,6,(a)=>{
                sim.nodes[a].queue+=2;sim.nodes[a].active=true;
                sim.log(`  PRIMARY → ${sim.nodes[a].label}: q=${sim.nodes[a].queue.toFixed(0)}`, '#4ade80');
              });
            },dl))(primary[i],primary[i+1],dd);
            dd+=1200;
          }
          // Backup: EU-W→EU-E→Asia-C→Asia-E (same, just smaller)
          const backup = [6, 10, 14];
          dd = 400;
          for (let i = 0; i < backup.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              sim.sendPacket(f,t,'rgba(45,212,191,0.5)',0.005,3,(a)=>{sim.nodes[a].queue+=1;});
            },dl))(backup[i],backup[i+1],dd);
            dd+=1300;
          }
          sim.log('  Backup: EU-W→Africa→Asia-E', '#2dd4bf');
        }, d + 800);

      } else if (sim._globalPhase === 1) {
        // SA(4) → Oceania(16)
        sim.log('━━━ SA → Oceania ━━━', 'var(--cyan)');
        sim.log('No cache → full DNS cascade', '#fb923c');
        const dnsPath = [4, 10, 14, 16];
        let d = 0;
        const labels = ['Africa','Asia-E','Oceania'];
        for (let i = 0; i < dnsPath.length-1; i++) {
          ((f,t,dl,idx) => setTimeout(() => {
            sim.sendPacket(f,t,'#fbbf24',0.006,3,(a)=>{
              sim.nodes[a].active=true;
              sim.log(`  DNS → ${labels[idx]}: ${idx<2?'forward':'FOUND!'}`, idx<2?'#fbbf24':'#4ade80');
            });
          },dl))(dnsPath[i],dnsPath[i+1],d,i);
          d += 1400;
        }
        setTimeout(() => {
          sim.log('Phase 2: Data via Africa...', '#4ade80');
          const data = [4, 10, 14, 16];
          let dd = 0;
          for (let i = 0; i < data.length-1; i++) {
            ((f,t,dl) => setTimeout(() => {
              const li = sim.links.findIndex(l=>(l.a===f&&l.b===t)||(l.a===t&&l.b===f));
              if(li>=0){sim.links[li].active=true;sim.links[li].activeColor='rgba(74,222,128,0.5)';sim.links[li].activeWidth=3;}
              sim.sendPacket(f,t,'#4ade80',0.005,6,(a)=>{
                sim.nodes[a].queue+=2;sim.nodes[a].active=true;
                sim.log(`  HOP → ${sim.nodes[a].label} [q:${sim.nodes[a].queue.toFixed(0)}]`, '#4ade80');
              });
            },dl))(data[i],data[i+1],dd);
            dd+=1300;
          }
        }, d + 800);

      } else {
        sim.log('━━━ Global health beacons ━━━', '#fbbf24');
        const gateways = [0,2,4,6,8,10,12,14,16];
        gateways.forEach((gw, i) => {
          setTimeout(() => {
            const nbs = sim.links.filter(l=>l.a===gw||l.b===gw).map(l=>l.a===gw?l.b:l.a).filter(n=>n%2===0).slice(0,2);
            nbs.forEach(nb => sim.sendPacket(gw, nb, 'rgba(251,191,36,0.3)', 0.008, 2, ()=>{}));
            sim.log(`  ${sim.nodes[gw].label} beacon → ${nbs.length} peers`, 'rgba(251,191,36,0.6)');
          }, i * 400);
        });
      }
    }
  }
});
