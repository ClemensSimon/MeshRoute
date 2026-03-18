// Algorithm simulations — four routing approaches
// State of the Art: Naive Flooding, Managed Flooding, Next-Hop
// New Proposal: System 5

// Shared node layout for fair comparison (same topology, different routing)
function makeSharedNodes(sim) {
  sim.nodes = []; sim.edges = []; sim.packets = [];
  const pos = [
    [0.08,0.35],[0.22,0.15],[0.22,0.5],[0.22,0.85],
    [0.42,0.2],[0.42,0.5],[0.42,0.8],
    [0.62,0.15],[0.62,0.5],[0.62,0.85],
    [0.82,0.3],[0.82,0.7],[0.94,0.5],
  ];
  pos.forEach((p, i) => sim.addNode(p[0], p[1], {
    r: (i === 0 || i === 12) ? 8 : 5,
    color: '#334155',
    stroke: 'rgba(148,163,184,0.3)',
    label: i === 0 ? 'SRC' : i === 12 ? 'DST' : '',
    suppressed: false,
    isRouter: false,
    nextHop: false,
  }));
  const edges = [
    [0,1],[0,2],[0,3],[1,4],[1,2],[2,5],[2,3],[3,6],
    [4,5],[4,7],[5,6],[5,8],[6,9],[7,8],[7,10],
    [8,9],[8,11],[9,11],[10,12],[11,12],[10,8],[5,7]
  ];
  edges.forEach(e => sim.addEdge(e[0], e[1]));
}

// ============================================================
//  1. NAIVE FLOODING — every node rebroadcasts everything
// ============================================================
const naiveSim = new MeshSim('canvas-naive-flood', {
  setup(sim) {
    makeSharedNodes(sim);
    sim._timer = 0;
    sim._visited = new Set();
    sim._txCount = 0;
  },
  drawOverlay(sim, ctx) {
    // TX counter
    ctx.font = '12px JetBrains Mono';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#f87171';
    ctx.fillText('TX: ' + sim._txCount, 8, sim.H - 8);
    // "every node lights up" indicator
    ctx.fillStyle = 'rgba(248,113,113,0.15)';
    ctx.fillRect(0, 0, sim.W, 3);
  },
  tick(sim) {
    sim._timer += 0.016;
    if (sim._timer > 4.5) {
      sim._timer = 0;
      sim._visited = new Set();
      sim._txCount = 0;
      sim.nodes.forEach(n => { n.color = '#334155'; n.stroke = 'rgba(148,163,184,0.3)'; });
      sim.nodes[0].color = '#f87171'; sim.nodes[0].stroke = '#f87171';
      sim._visited.add(0);
      naiveFloodFrom(sim, 0);
    }
  }
});
function naiveFloodFrom(sim, id) {
  const neighbors = sim.edges
    .filter(e => e.a === id || e.b === id)
    .map(e => e.a === id ? e.b : e.a)
    .filter(n => !sim._visited.has(n));
  neighbors.forEach((n, i) => {
    sim._visited.add(n);
    setTimeout(() => {
      sim._txCount++;
      sim.sendPacket(id, n, '#f87171', 0.025, (arrived) => {
        sim.nodes[arrived].color = '#f87171';
        sim.nodes[arrived].stroke = '#f87171';
        naiveFloodFrom(sim, arrived);
      });
    }, i * 60);
  });
}
naiveSim.start();

// ============================================================
//  2. MANAGED FLOODING — SNR-based suppression
// ============================================================
const managedSim = new MeshSim('canvas-managed-flood', {
  setup(sim) {
    makeSharedNodes(sim);
    sim._timer = 0;
    sim._visited = new Set();
    sim._suppressed = new Set();
    sim._txCount = 0;
    // Mark ~2 nodes as ROUTER role (always rebroadcast)
    sim.nodes[5].isRouter = true;
    sim.nodes[8].isRouter = true;
    sim.nodes[5].label = 'R';
    sim.nodes[8].label = 'R';
  },
  drawOverlay(sim, ctx) {
    ctx.font = '12px JetBrains Mono';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#fbbf24';
    ctx.fillText('TX: ' + sim._txCount, 8, sim.H - 8);
    // Suppressed count
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('Suppressed: ' + sim._suppressed.size, 80, sim.H - 8);
  },
  tick(sim) {
    sim._timer += 0.016;
    if (sim._timer > 4.5) {
      sim._timer = 0;
      sim._visited = new Set();
      sim._suppressed = new Set();
      sim._txCount = 0;
      sim.nodes.forEach(n => {
        n.color = '#334155'; n.stroke = 'rgba(148,163,184,0.3)'; n.suppressed = false;
      });
      sim.nodes[5].label = 'R'; sim.nodes[8].label = 'R';
      sim.nodes[0].color = '#fbbf24'; sim.nodes[0].stroke = '#fbbf24';
      sim._visited.add(0);
      managedFloodFrom(sim, 0);
    }
  }
});
function managedFloodFrom(sim, id) {
  const neighbors = sim.edges
    .filter(e => e.a === id || e.b === id)
    .map(e => e.a === id ? e.b : e.a)
    .filter(n => !sim._visited.has(n));
  neighbors.forEach((n, i) => {
    sim._visited.add(n);
    setTimeout(() => {
      sim._txCount++;
      sim.sendPacket(id, n, '#fbbf24', 0.025, (arrived) => {
        const node = sim.nodes[arrived];
        // SNR suppression: if a neighbor already rebroadcasted AND this node is not a router
        const neighborAlreadyBroadcast = sim.edges
          .filter(e => e.a === arrived || e.b === arrived)
          .map(e => e.a === arrived ? e.b : e.a)
          .some(nb => nb !== id && sim._visited.has(nb) && !sim._suppressed.has(nb) && nb !== 0);

        if (!node.isRouter && neighborAlreadyBroadcast && Math.random() < 0.55) {
          // SUPPRESSED — node heard a rebroadcast, doesn't rebroadcast
          node.color = '#475569'; // dim gray = suppressed
          node.stroke = '#64748b';
          node.suppressed = true;
          sim._suppressed.add(arrived);
        } else {
          // REBROADCAST
          node.color = '#fbbf24';
          node.stroke = '#fbbf24';
          managedFloodFrom(sim, arrived);
        }
      });
    }, i * 80);
  });
}
managedSim.start();

// ============================================================
//  3. NEXT-HOP ROUTING — learn relay, then direct
// ============================================================
const nextHopSim = new MeshSim('canvas-next-hop', {
  setup(sim) {
    makeSharedNodes(sim);
    sim._timer = 0;
    sim._phase = 0; // 0=flood-learn, 1=direct, 2=direct, 3=fail+fallback
    sim._txCount = 0;
    sim._learnedPath = [0, 2, 5, 8, 11, 12]; // path learned after first flood
    sim._nextHopNode = 2; // first relay
  },
  drawOverlay(sim, ctx) {
    ctx.font = '12px JetBrains Mono';
    ctx.textAlign = 'left';
    const phaseLabels = ['Phase 1: Flood & Learn', 'Phase 2: Next-Hop Direct', 'Phase 2: Next-Hop Direct', 'Phase 3: Fallback'];
    const phaseColors = ['#fb923c', '#4ade80', '#4ade80', '#f87171'];
    ctx.fillStyle = phaseColors[sim._phase % 4];
    ctx.fillText(phaseLabels[sim._phase % 4], 8, sim.H - 8);
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('TX: ' + sim._txCount, sim.W - 70, sim.H - 8);
  },
  tick(sim) {
    sim._timer += 0.016;
    if (sim._timer > 4) {
      sim._timer = 0;
      sim._txCount = 0;
      sim.nodes.forEach(n => { n.color = '#334155'; n.stroke = 'rgba(148,163,184,0.3)'; n.nextHop = false; });
      sim.nodes[0].color = '#fb923c'; sim.nodes[0].stroke = '#fb923c';
      sim.nodes[0].label = 'SRC';
      sim.nodes[12].label = 'DST';

      const phase = sim._phase % 4;

      if (phase === 0) {
        // Flood & learn — like managed flooding, then highlight learned path
        sim._visited = new Set([0]);
        nextHopFloodLearn(sim, 0);
      } else if (phase === 1 || phase === 2) {
        // Direct via next-hop — only the learned path lights up
        sim.nodes[sim._nextHopNode].nextHop = true;
        sim.nodes[sim._nextHopNode].label = 'NH';
        const path = sim._learnedPath;
        let delay = 0;
        for (let i = 0; i < path.length - 1; i++) {
          ((from, to, d) => {
            setTimeout(() => {
              sim._txCount++;
              sim.sendPacket(from, to, '#4ade80', 0.03, (arrived) => {
                sim.nodes[arrived].color = '#4ade80';
                sim.nodes[arrived].stroke = '#4ade80';
              });
            }, d);
          })(path[i], path[i+1], delay);
          delay += 350;
        }
      } else {
        // Fail & fallback — next-hop dies, fall back to flood
        sim.nodes[sim._nextHopNode].color = '#f87171';
        sim.nodes[sim._nextHopNode].label = 'X';
        sim.nodes[sim._nextHopNode].stroke = '#f87171';
        // Try next-hop, fail
        setTimeout(() => {
          sim._txCount++;
          sim.sendPacket(0, sim._nextHopNode, '#f87171', 0.03, () => {
            // Failed — fallback to flood
            setTimeout(() => {
              sim.nodes[0].color = '#fbbf24';
              const altPath = [0, 1, 4, 7, 10, 12];
              let d2 = 0;
              for (let i = 0; i < altPath.length - 1; i++) {
                ((from, to, d) => {
                  setTimeout(() => {
                    sim._txCount++;
                    sim.sendPacket(from, to, '#fbbf24', 0.03, (arrived) => {
                      sim.nodes[arrived].color = '#fbbf24';
                      sim.nodes[arrived].stroke = '#fbbf24';
                    });
                  }, d);
                })(altPath[i], altPath[i+1], d2);
                d2 += 350;
              }
            }, 300);
          });
        }, 200);
      }
      sim._phase++;
    }
  }
});
function nextHopFloodLearn(sim, id) {
  const neighbors = sim.edges
    .filter(e => e.a === id || e.b === id)
    .map(e => e.a === id ? e.b : e.a)
    .filter(n => !sim._visited.has(n));
  neighbors.forEach((n, i) => {
    sim._visited.add(n);
    setTimeout(() => {
      sim._txCount++;
      const isOnPath = sim._learnedPath.includes(id) && sim._learnedPath.includes(n);
      const color = isOnPath ? '#4ade80' : '#fb923c';
      sim.sendPacket(id, n, color, 0.025, (arrived) => {
        sim.nodes[arrived].color = isOnPath ? '#4ade80' : '#78716c';
        sim.nodes[arrived].stroke = isOnPath ? '#4ade80' : '#78716c';
        if (Math.random() < 0.6 || sim.nodes[arrived].isRouter) {
          nextHopFloodLearn(sim, arrived);
        }
      });
    }, i * 80);
  });
}
nextHopSim.start();

// ============================================================
//  4. SYSTEM 5 — Geo-clustered multi-path load-balanced
// ============================================================
const sys5Sim = new MeshSim('canvas-system5', {
  setup(sim) {
    makeSharedNodes(sim);
    sim._timer = 0;
    // Assign clusters
    sim.nodes.forEach((n, i) => {
      if (i <= 3) { n._cluster = 0; n.stroke = 'rgba(167,139,250,0.5)'; }
      else if (i <= 6) { n._cluster = 1; n.stroke = 'rgba(34,211,238,0.5)'; }
      else if (i <= 9) { n._cluster = 2; n.stroke = 'rgba(20,184,166,0.5)'; }
      else { n._cluster = 3; n.stroke = 'rgba(251,146,60,0.5)'; }
    });
    // Mark border nodes
    [3,4,6,7,9,10].forEach(i => { sim.nodes[i].label = 'B'; });
    sim.nodes[0].label = 'SRC'; sim.nodes[12].label = 'DST';

    sim._routes = [
      { path: [0,1,4,7,10,12], weight: 0.45, color: '#22d3ee' },
      { path: [0,2,5,8,11,12], weight: 0.35, color: '#2dd4bf' },
      { path: [0,3,6,9,11,12], weight: 0.20, color: '#a78bfa' },
    ];
    sim.nodes.forEach(n => { n.load = 0; });
  },
  drawClusters(sim, ctx) {
    const clusters = [
      { nodes: [0,1,2,3], color: 'rgba(167,139,250,0.06)', cx: 0.15, cy: 0.46 },
      { nodes: [4,5,6], color: 'rgba(34,211,238,0.06)', cx: 0.42, cy: 0.5 },
      { nodes: [7,8,9], color: 'rgba(20,184,166,0.06)', cx: 0.62, cy: 0.5 },
      { nodes: [10,11,12], color: 'rgba(251,146,60,0.06)', cx: 0.86, cy: 0.5 },
    ];
    clusters.forEach(cl => {
      ctx.beginPath();
      ctx.arc(cl.cx * sim.W, cl.cy * sim.H, sim.W * 0.13, 0, PI2);
      ctx.fillStyle = cl.color;
      ctx.fill();
      ctx.strokeStyle = cl.color.replace('0.06', '0.12');
      ctx.lineWidth = 1; ctx.setLineDash([3,3]); ctx.stroke(); ctx.setLineDash([]);
    });
  },
  drawOverlay(sim, ctx) {
    // Route weights
    const y = sim.H - 15;
    ctx.font = '10px JetBrains Mono';
    ctx.textAlign = 'left';
    sim._routes.forEach((r, i) => {
      ctx.fillStyle = r.color;
      ctx.fillRect(8, y - 36 + i * 13, 8, 8);
      ctx.fillStyle = '#94a3b8';
      ctx.fillText(`R${i+1}: ${Math.round(r.weight*100)}%`, 20, y - 29 + i * 13);
    });
    // Load bars on nodes
    sim.nodes.forEach(n => {
      if (n.load > 0.01) {
        const bw = 18, bh = 3;
        ctx.fillStyle = 'rgba(0,0,0,0.4)';
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 3, bw, bh);
        const lc = n.load > 0.7 ? '#f87171' : n.load > 0.4 ? '#fbbf24' : '#4ade80';
        ctx.fillStyle = lc;
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 3, bw * n.load, bh);
      }
    });
    // TX count
    ctx.fillStyle = '#22d3ee';
    ctx.font = '12px JetBrains Mono';
    ctx.textAlign = 'right';
    ctx.fillText('TX: ' + (sim._txCount || 0), sim.W - 8, sim.H - 8);
  },
  tick(sim) {
    sim._timer += 0.016;
    sim.nodes.forEach(n => { n.load = Math.max(0, n.load - 0.003); });
    if (sim._timer > 0.9) {
      sim._timer = 0;
      if (!sim._txCount) sim._txCount = 0;
      // Select route proportionally
      const r = Math.random();
      let cumul = 0, route = sim._routes[0];
      for (const rt of sim._routes) { cumul += rt.weight; if (r <= cumul) { route = rt; break; } }
      let delay = 0;
      for (let i = 0; i < route.path.length - 1; i++) {
        ((from, to, d, col) => {
          setTimeout(() => {
            sim._txCount++;
            sim.sendPacket(from, to, col, 0.035, (arrived) => {
              sim.nodes[arrived].load = Math.min(1, sim.nodes[arrived].load + 0.15);
              sim.nodes[arrived].color = col;
            });
          }, d);
        })(route.path[i], route.path[i+1], delay, route.color);
        delay += 220;
      }
      // Rebalance weights based on load
      sim._routes.forEach(rt => {
        let maxLoad = 0;
        rt.path.forEach(id => { maxLoad = Math.max(maxLoad, sim.nodes[id].load); });
        rt.weight = Math.max(0.05, (1 - maxLoad) * rt.path.length / 5);
      });
      const total = sim._routes.reduce((s, r) => s + r.weight, 0);
      sim._routes.forEach(r => r.weight = r.weight / total);
    }
  }
});
sys5Sim.start();
