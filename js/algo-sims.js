// Algorithm simulations: Flooding, Tree, Multi-Path, Geo-Cluster, System 5

// ============================================================
//  1. FLOODING
// ============================================================
const floodSim = new MeshSim('canvas-flood', {
  setup(sim) {
    sim.nodes = []; sim.edges = []; sim.packets = [];
    sim._floodTimer = 0; sim._visited = new Set();
    const pos = [
      [0.15,0.2],[0.35,0.15],[0.55,0.12],[0.78,0.2],[0.9,0.35],
      [0.1,0.5],[0.3,0.45],[0.5,0.4],[0.7,0.45],[0.88,0.55],
      [0.15,0.8],[0.35,0.75],[0.55,0.7],[0.75,0.78],[0.9,0.82],
      [0.45,0.55],[0.25,0.6],[0.65,0.6]
    ];
    pos.forEach((p, i) => sim.addNode(p[0], p[1], { r: 5, color: '#334155', stroke: 'rgba(148,163,184,0.3)' }));
    for (let i = 0; i < sim.nodes.length; i++)
      for (let j = i + 1; j < sim.nodes.length; j++)
        if (dist(sim.nodes[i], sim.nodes[j]) < sim.W * 0.28)
          sim.addEdge(i, j);
  },
  tick(sim) {
    sim._floodTimer += 0.016;
    if (sim._floodTimer > 4) {
      sim._floodTimer = 0;
      sim._visited = new Set();
      sim.nodes.forEach(n => n.color = '#334155');
      const src = 0;
      sim.nodes[src].color = '#f87171';
      sim._visited.add(src);
      sim._floodFrom(src);
    }
  }
});
floodSim._floodFrom = function(id) {
  const neighbors = this.edges
    .filter(e => e.a === id || e.b === id)
    .map(e => e.a === id ? e.b : e.a)
    .filter(n => !this._visited.has(n));
  neighbors.forEach((n, i) => {
    this._visited.add(n);
    setTimeout(() => {
      this.sendPacket(id, n, '#f87171', 0.025, (arrived) => {
        this.nodes[arrived].color = '#f87171';
        this._floodFrom(arrived);
      });
    }, i * 80);
  });
};
floodSim.start();

// ============================================================
//  2. SPANNING TREE
// ============================================================
const treeSim = new MeshSim('canvas-tree', {
  setup(sim) {
    sim.nodes = []; sim.edges = []; sim.packets = [];
    sim._treeTimer = 0;
    const pos = [
      [0.5, 0.1],
      [0.25, 0.32], [0.75, 0.32],
      [0.12, 0.56], [0.38, 0.56], [0.62, 0.56], [0.88, 0.56],
      [0.06, 0.82], [0.2, 0.82], [0.35, 0.82], [0.5, 0.82], [0.65, 0.82], [0.8, 0.82], [0.94, 0.82]
    ];
    pos.forEach((p, i) => sim.addNode(p[0], p[1], {
      r: i === 0 ? 8 : 5,
      color: i === 0 ? '#fbbf24' : '#334155',
      stroke: i === 0 ? '#fbbf24' : 'rgba(148,163,184,0.3)',
      label: i === 0 ? 'ROOT' : ''
    }));
    const treeEdges = [[0,1],[0,2],[1,3],[1,4],[2,5],[2,6],[3,7],[3,8],[4,9],[4,10],[5,11],[5,12],[6,13]];
    treeEdges.forEach(e => sim.addEdge(e[0], e[1], { color: 'rgba(251,191,36,0.2)' }));
  },
  tick(sim) {
    sim._treeTimer += 0.016;
    if (sim._treeTimer > 3.5) {
      sim._treeTimer = 0;
      sim.nodes.forEach((n, i) => { n.color = i === 0 ? '#fbbf24' : '#334155'; });
      // send from leaf 8 to leaf 12: 8->3->1->0->2->5->12
      const path = [8, 3, 1, 0, 2, 5, 12];
      let delay = 0;
      for (let i = 0; i < path.length - 1; i++) {
        ((from, to, d) => {
          setTimeout(() => {
            sim.sendPacket(from, to, '#fbbf24', 0.03, (arrived) => {
              sim.nodes[arrived].color = '#fbbf24';
            });
          }, d);
        })(path[i], path[i + 1], delay);
        delay += 450;
      }
    }
  }
});
treeSim.start();

// ============================================================
//  3. MULTI-PATH
// ============================================================
const multiSim = new MeshSim('canvas-multi', {
  setup(sim) {
    sim.nodes = []; sim.edges = []; sim.packets = [];
    sim._multiTimer = 0;
    const pos = [
      [0.08, 0.5], [0.25, 0.2], [0.25, 0.5], [0.25, 0.8],
      [0.5, 0.2], [0.5, 0.5], [0.5, 0.8],
      [0.75, 0.2], [0.75, 0.5], [0.75, 0.8],
      [0.92, 0.5],
    ];
    pos.forEach((p, i) => sim.addNode(p[0], p[1], {
      r: (i === 0 || i === 10) ? 8 : 5,
      color: i === 0 ? '#4ade80' : i === 10 ? '#4ade80' : '#334155',
      stroke: (i === 0 || i === 10) ? '#4ade80' : 'rgba(148,163,184,0.3)',
      label: i === 0 ? 'SRC' : i === 10 ? 'DST' : ''
    }));
    const edges = [[0,1],[0,2],[0,3],[1,4],[2,5],[3,6],[4,7],[5,8],[6,9],[7,10],[8,10],[9,10],[1,2],[4,5],[5,6],[7,8],[8,9],[2,4],[5,7]];
    edges.forEach(e => sim.addEdge(e[0], e[1]));
    sim._paths = [[0,1,4,7,10],[0,2,5,8,10],[0,3,6,9,10]];
    sim._pathColors = ['#4ade80', '#2dd4bf', '#22d3ee'];
    sim._activePath = 0;
  },
  tick(sim) {
    sim._multiTimer += 0.016;
    if (sim._multiTimer > 3) {
      sim._multiTimer = 0;
      sim.nodes.forEach((n, i) => { if (i !== 0 && i !== 10) n.color = '#334155'; });
      const pathIdx = sim._activePath % 4;
      sim.edges.forEach(e => e.color = 'rgba(100,116,139,0.2)');
      if (pathIdx < 3) {
        const path = sim._paths[pathIdx];
        for (let i = 0; i < path.length - 1; i++) {
          const ei = sim.edges.findIndex(e =>
            (e.a === path[i] && e.b === path[i+1]) || (e.a === path[i+1] && e.b === path[i])
          );
          if (ei >= 0) sim.edges[ei].color = sim._pathColors[pathIdx] + '80';
        }
        let delay = 0;
        for (let i = 0; i < path.length - 1; i++) {
          ((from, to, d) => {
            setTimeout(() => {
              sim.sendPacket(from, to, sim._pathColors[pathIdx], 0.03, (arrived) => {
                sim.nodes[arrived].color = sim._pathColors[pathIdx];
              });
            }, d);
          })(path[i], path[i+1], delay);
          delay += 400;
        }
      } else {
        // failover demo
        const brokenEdge = sim.edges.findIndex(e => (e.a === 4 && e.b === 7) || (e.a === 7 && e.b === 4));
        if (brokenEdge >= 0) sim.edges[brokenEdge].color = '#f8717180';
        const failPath = [0, 1, 4]; // goes to node 4, then fails
        let delay = 0;
        for (let i = 0; i < failPath.length - 1; i++) {
          ((from, to, d) => {
            setTimeout(() => {
              sim.sendPacket(from, to, '#f87171', 0.03, (arrived) => {
                sim.nodes[arrived].color = '#f87171';
                if (arrived === 4) {
                  sim.nodes[4].color = '#f87171';
                  setTimeout(() => {
                    // reroute via edge 4->5, 5->8, 8->10
                    const repath = [4, 5, 8, 10];
                    let rd = 0;
                    for (let ri = 0; ri < repath.length - 1; ri++) {
                      ((rf, rt, rdd) => {
                        setTimeout(() => {
                          sim.sendPacket(rf, rt, '#4ade80', 0.03, (a) => { sim.nodes[a].color = '#4ade80'; });
                        }, rdd);
                      })(repath[ri], repath[ri+1], rd);
                      rd += 350;
                    }
                  }, 200);
                }
              });
            }, d);
          })(failPath[i], failPath[i+1], delay);
          delay += 400;
        }
      }
      sim._activePath++;
    }
  }
});
multiSim.start();

// ============================================================
//  4. GEO-CLUSTER
// ============================================================
const geoSim = new MeshSim('canvas-geo', {
  setup(sim) {
    sim.nodes = []; sim.edges = []; sim.packets = [];
    sim._geoTimer = 0;
    const cA = [[0.1,0.3],[0.18,0.5],[0.12,0.7],[0.25,0.4],[0.22,0.65]];
    const cB = [[0.42,0.25],[0.5,0.45],[0.45,0.65],[0.55,0.3],[0.52,0.7]];
    const cC = [[0.75,0.3],[0.82,0.5],[0.78,0.7],[0.88,0.35],[0.85,0.65]];
    const all = [...cA, ...cB, ...cC];
    all.forEach((p, i) => {
      const cluster = i < 5 ? 0 : i < 10 ? 1 : 2;
      const colors = ['#a78bfa', '#22d3ee', '#fb923c'];
      sim.addNode(p[0], p[1], {
        r: 5, color: colors[cluster] + '40', stroke: colors[cluster],
        cluster, label: (i === 3 || i === 4 || i === 6 || i === 8 || i === 10 || i === 12) ? 'B' : ''
      });
    });
    for (let c = 0; c < 3; c++) {
      const base = c * 5;
      for (let i = base; i < base + 5; i++)
        for (let j = i + 1; j < base + 5; j++)
          if (dist(sim.nodes[i], sim.nodes[j]) < sim.W * 0.22)
            sim.addEdge(i, j, { color: ['rgba(167,139,250,0.15)', 'rgba(34,211,238,0.15)', 'rgba(251,146,60,0.15)'][c] });
    }
    // inter-cluster border edges
    sim.addEdge(3, 6, { color: 'rgba(148,163,184,0.3)', width: 2 });
    sim.addEdge(4, 8, { color: 'rgba(148,163,184,0.3)', width: 2 });
    sim.addEdge(9, 10, { color: 'rgba(148,163,184,0.3)', width: 2 });
    sim.addEdge(7, 12, { color: 'rgba(148,163,184,0.3)', width: 2 });
    sim._clusters = [
      { nodes: [0,1,2,3,4], color: 'rgba(167,139,250,0.06)', cx: 0.17, cy: 0.5 },
      { nodes: [5,6,7,8,9], color: 'rgba(34,211,238,0.06)', cx: 0.49, cy: 0.47 },
      { nodes: [10,11,12,13,14], color: 'rgba(251,146,60,0.06)', cx: 0.82, cy: 0.5 },
    ];
  },
  drawClusters(sim, ctx) {
    sim._clusters.forEach(cl => {
      ctx.beginPath();
      ctx.arc(cl.cx * sim.W, cl.cy * sim.H, sim.W * 0.17, 0, PI2);
      ctx.fillStyle = cl.color;
      ctx.fill();
      ctx.strokeStyle = cl.color.replace('0.06', '0.15');
      ctx.lineWidth = 1; ctx.stroke();
    });
  },
  tick(sim) {
    sim._geoTimer += 0.016;
    if (sim._geoTimer > 4) {
      sim._geoTimer = 0;
      // compute paths dynamically using BFS over actual edges
      if (!sim._geoPaths) {
        sim._geoPaths = findKPaths(sim.edges, 0, 14, 2);
      }
      const primary = sim._geoPaths[0] || [];
      const backup = sim._geoPaths[1] || [];
      let delay = 0;
      for (let i = 0; i < primary.length - 1; i++) {
        ((from, to, d) => {
          setTimeout(() => {
            sim.sendPacket(from, to, '#22d3ee', 0.025, () => {});
          }, d);
        })(primary[i], primary[i+1], delay);
        delay += 500;
      }
      if (backup.length > 0) {
        setTimeout(() => {
          let d2 = 0;
          for (let i = 0; i < backup.length - 1; i++) {
            ((from, to, d) => {
              setTimeout(() => {
                sim.sendPacket(from, to, 'rgba(34,211,238,0.4)', 0.02, () => {});
              }, d);
            })(backup[i], backup[i+1], d2);
            d2 += 500;
          }
        }, 200);
      }
    }
  }
});
geoSim.start();

// ============================================================
//  5. SYSTEM 5 — WINNER
// ============================================================
const winSim = new MeshSim('canvas-winner', {
  setup(sim) {
    sim.nodes = []; sim.edges = []; sim.packets = [];
    sim._winTimer = 0;
    const pos = [
      [0.06,0.45],[0.22,0.2],[0.22,0.5],[0.22,0.8],
      [0.42,0.15],[0.42,0.45],[0.42,0.75],
      [0.62,0.2],[0.62,0.5],[0.62,0.8],
      [0.82,0.3],[0.82,0.65],[0.94,0.48],
    ];
    pos.forEach((p, i) => sim.addNode(p[0], p[1], {
      r: (i === 0 || i === 12) ? 9 : 5,
      color: (i === 0 || i === 12) ? '#22d3ee' : '#334155',
      stroke: (i === 0 || i === 12) ? '#22d3ee' : 'rgba(148,163,184,0.3)',
      label: i === 0 ? 'SRC' : i === 12 ? 'DST' : '',
      load: 0, battery: rand(0.4, 1)
    }));
    const edges = [[0,1],[0,2],[0,3],[1,4],[1,2],[2,5],[2,3],[3,6],[4,5],[4,7],[5,6],[5,8],[6,9],[7,8],[7,10],[8,9],[8,11],[9,11],[10,12],[11,12],[10,8],[5,7]];
    edges.forEach(e => sim.addEdge(e[0], e[1]));
    sim._routes = [
      { path: [0,1,4,7,10,12], weight: 0.45, color: '#22d3ee' },
      { path: [0,2,5,8,11,12], weight: 0.35, color: '#2dd4bf' },
      { path: [0,3,6,9,11,12], weight: 0.20, color: '#a78bfa' },
    ];
  },
  drawOverlay(sim, ctx) {
    const y = sim.H - 15;
    ctx.font = '11px JetBrains Mono';
    ctx.textAlign = 'left';
    sim._routes.forEach((r, i) => {
      ctx.fillStyle = r.color;
      ctx.fillRect(10, y - 38 + i * 14, 8, 8);
      ctx.fillStyle = '#94a3b8';
      ctx.fillText(`Route ${i+1}: ${Math.round(r.weight*100)}%`, 24, y - 31 + i * 14);
    });
    sim.nodes.forEach(n => {
      if (n.load > 0) {
        const bw = 20, bh = 3;
        ctx.fillStyle = 'rgba(0,0,0,0.4)';
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 3, bw, bh);
        const lc = n.load > 0.7 ? '#f87171' : n.load > 0.4 ? '#fbbf24' : '#4ade80';
        ctx.fillStyle = lc;
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 3, bw * n.load, bh);
      }
    });
  },
  tick(sim) {
    sim._winTimer += 0.016;
    sim.nodes.forEach(n => { n.load = Math.max(0, n.load - 0.003); });
    if (sim._winTimer > 0.8) {
      sim._winTimer = 0;
      const r = Math.random();
      let cumul = 0, route = sim._routes[0];
      for (const rt of sim._routes) { cumul += rt.weight; if (r <= cumul) { route = rt; break; } }
      let delay = 0;
      for (let i = 0; i < route.path.length - 1; i++) {
        ((from, to, d, col) => {
          setTimeout(() => {
            sim.sendPacket(from, to, col, 0.035, (arrived) => {
              sim.nodes[arrived].load = Math.min(1, sim.nodes[arrived].load + 0.15);
            });
          }, d);
        })(route.path[i], route.path[i+1], delay, route.color);
        delay += 250;
      }
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
winSim.start();
