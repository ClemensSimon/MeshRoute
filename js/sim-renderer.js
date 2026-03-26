// MeshRoute Simulator - Canvas Renderer

// ---- Canvas Renderer ----
class SimRenderer {
  constructor(canvasId, labelClass) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.labelClass = labelClass;
    this.net = null;
    this.animPackets = [];
    this.flashNodes = {}; // nodeId -> {color, alpha}
    this.reachedNodes = new Map();  // nodeId -> intensity (0-1), fades with each new hop wave
    this.reachedEdges = new Map();  // "a-b" -> intensity (0-1)
    this.deliveryNodes = new Set(); // nodes on the successful delivery path (green)
    this.deliveryEdges = new Set(); // edges on the successful delivery path (green)
    this.markedSrc = -1;
    this.markedDst = -1;
    this.stats = { tx: 0, delivered: 0, sent: 0, totalHops: 0, lastHops: 0 };
    this.syncPeer = null;
    this.hopLimitRings = [];
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this._setupInteraction();
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    this.W = this.canvas.width = r.width;
    this.H = this.canvas.height = r.height;
  }

  _syncToPeer() {
    if (this.syncPeer) {
      this.syncPeer.panX = this.panX;
      this.syncPeer.panY = this.panY;
      this.syncPeer.zoom = this.zoom;
    }
  }

  _setupInteraction() {
    let dragging = false, lastX, lastY;
    this.canvas.addEventListener('mousedown', e => {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      this.canvas.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', e => {
      if (!dragging) return;
      this.panX += e.clientX - lastX; this.panY += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      this._syncToPeer();
    });
    window.addEventListener('mouseup', () => {
      dragging = false;
      this.canvas.style.cursor = simState.started ? 'grab' : 'crosshair';
    });
    this.canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
      const rect = this.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      this.panX = mx - (mx - this.panX) * zoomFactor;
      this.panY = my - (my - this.panY) * zoomFactor;
      this.zoom *= zoomFactor;
      this.zoom = Math.max(0.3, Math.min(10, this.zoom));
      this._syncToPeer();
    }, { passive: false });
    this.canvas.style.cursor = 'crosshair';
  }

  setNetwork(net) {
    this.net = net;
    this.animPackets = [];
    this.flashNodes = {};
    this.reachedNodes = new Map();
    this.reachedEdges = new Map();
    this.deliveryNodes = new Set();
    this.deliveryEdges = new Set();
    this.stats = { tx: 0, delivered: 0, sent: 0, totalHops: 0, lastHops: 0 };
    this.fitNetwork();
  }

  clearReached() {
    this.reachedNodes = new Map();
    this.reachedEdges = new Map();
  }

  // Dim all existing reached markers by a factor (called before each new hop wave)
  fadeReached(factor = 0.55) {
    for (const [id, intensity] of this.reachedNodes) {
      this.reachedNodes.set(id, intensity * factor);
    }
    for (const [key, intensity] of this.reachedEdges) {
      this.reachedEdges.set(key, intensity * factor);
    }
  }

  markReached(nodeId) {
    this.reachedNodes.set(nodeId, 1.0);
  }

  markEdgeReached(fromId, toId) {
    const key = Math.min(fromId, toId) + '-' + Math.max(fromId, toId);
    this.reachedEdges.set(key, 1.0);
  }

  markDeliveryPath(path) {
    // Mark the successful delivery path in green
    if (!path || path.length < 2) return;
    for (const nid of path) this.deliveryNodes.add(nid);
    for (let i = 0; i < path.length - 1; i++) {
      const key = Math.min(path[i], path[i+1]) + '-' + Math.max(path[i], path[i+1]);
      this.deliveryEdges.add(key);
    }
  }

  fitNetwork() {
    if (!this.net) return;
    // Find actual bounding box of all alive nodes
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of this.net.nodes) {
      if (n.battery <= 0) continue;
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    }
    if (!isFinite(minX)) { minX = 0; maxX = this.net.area; minY = 0; maxY = this.net.area; }
    const spanX = (maxX - minX) || 1;
    const spanY = (maxY - minY) || 1;
    const margin = 50;
    const scaleX = (this.W - margin * 2) / spanX;
    const scaleY = (this.H - margin * 2) / spanY;
    this.scale = Math.min(scaleX, scaleY);
    this.zoom = 1;
    // Center the network
    this.panX = margin + ((this.W - margin * 2) - spanX * this.scale) / 2 - minX * this.scale;
    this.panY = margin + ((this.H - margin * 2) - spanY * this.scale) / 2 - minY * this.scale;
    this._syncToPeer();
  }

  toScreen(x, y) {
    return [this.panX + x * this.scale * this.zoom, this.panY + y * this.scale * this.zoom];
  }

  addPacket(from, to, color, onDone) {
    this.animPackets.push({ from, to, t: 0, color, onDone });
  }

  flashNode(id, color) {
    this.flashNodes[id] = { color, alpha: 1.0 };
  }

  update(dt) {
    // Animate packets
    const speed = 0.02 * simState.speed;
    for (let i = this.animPackets.length - 1; i >= 0; i--) {
      const p = this.animPackets[i];
      p.t += speed;
      if (p.t >= 1) {
        if (p.onDone) p.onDone();
        this.animPackets.splice(i, 1);
      }
    }
    // Fade flash nodes
    for (const id in this.flashNodes) {
      this.flashNodes[id].alpha -= 0.015;
      if (this.flashNodes[id].alpha <= 0) delete this.flashNodes[id];
    }
    // Move mobile nodes — stay within their cluster region
    if (this.net) {
      const bounds = this.net.clusterBounds;
      for (const n of this.net.nodes) {
        if (!n.mobile || n.battery <= 0) continue;
        n.heading += (Math.random() - 0.5) * 0.2;
        const s = n.speed * dt * 50 * simState.speed;
        n.x += Math.cos(n.heading) * s;
        n.y += Math.sin(n.heading) * s;

        // Constrain to cluster bounding box (with padding)
        const cb = bounds && bounds[n.cluster];
        if (cb) {
          const pad = this.net.range * 0.3;
          const minX = cb.minX - pad, maxX = cb.maxX + pad;
          const minY = cb.minY - pad, maxY = cb.maxY + pad;
          if (n.x < minX || n.x > maxX) { n.heading = Math.PI - n.heading; n.x = Math.max(minX, Math.min(maxX, n.x)); }
          if (n.y < minY || n.y > maxY) { n.heading = -n.heading; n.y = Math.max(minY, Math.min(maxY, n.y)); }
        } else {
          // Fallback: bounce off area edges
          if (n.x < 0 || n.x > this.net.area) { n.heading = Math.PI - n.heading; n.x = Math.max(0, Math.min(this.net.area, n.x)); }
          if (n.y < 0 || n.y > this.net.area) { n.heading = -n.heading; n.y = Math.max(0, Math.min(this.net.area, n.y)); }
        }
      }
    }
  }

  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);
    if (!this.net) return;

    // Draw cluster background regions
    if (this.net.clusterBounds) {
      const pad = 20; // padding around cluster bounds
      for (const [cid, b] of Object.entries(this.net.clusterBounds)) {
        const [x1, y1] = this.toScreen(b.minX, b.minY);
        const [x2, y2] = this.toScreen(b.maxX, b.maxY);
        const left = Math.min(x1, x2) - pad;
        const top = Math.min(y1, y2) - pad;
        const w = Math.abs(x2 - x1) + pad * 2;
        const h = Math.abs(y2 - y1) + pad * 2;
        const ci = +cid % CLUSTER_PASTEL.length;

        // Fill
        ctx.beginPath();
        ctx.roundRect(left, top, w, h, 12);
        ctx.fillStyle = CLUSTER_PASTEL[ci];
        ctx.fill();

        // Border
        ctx.strokeStyle = CLUSTER_BORDER_PASTEL[ci];
        ctx.lineWidth = 1;
        ctx.stroke();

        // Label
        ctx.font = '10px JetBrains Mono';
        ctx.fillStyle = CLUSTER_BORDER_PASTEL[ci];
        ctx.textAlign = 'left';
        ctx.fillText(`C${cid}`, left + 6, top + 14);
      }
    }

    // Draw links
    const bridges = this.net.bridgeLinks || new Set();
    const isLargeNet = this.net.nodes.length > 200;
    for (let li = 0; li < this.net.links.length; li++) {
      const l = this.net.links[li];

      // Dead links — never draw (visual clutter, no useful info)
      if (!l.alive) continue;

      // On large networks: skip very low quality links (visual clutter)
      if (isLargeNet && l.quality < 0.15) continue;
      const [ax, ay] = this.toScreen(this.net.nodes[l.a].x, this.net.nodes[l.a].y);
      const [bx, by] = this.toScreen(this.net.nodes[l.b].x, this.net.nodes[l.b].y);
      const edgeKey = Math.min(l.a, l.b) + '-' + Math.max(l.a, l.b);
      const edgeIntensity = this.reachedEdges.get(edgeKey) || 0;
      const isBridge = bridges.has(li);

      const isDelivery = this.deliveryEdges.has(edgeKey);

      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
      if (isDelivery) {
        // Delivery path — green for managed, purple for walkflood
        ctx.strokeStyle = this.deliveryColor || 'rgba(74,222,128,0.9)';
        ctx.lineWidth = 3;
      } else if (edgeIntensity > 0.01) {
        // Reached — yellow
        ctx.strokeStyle = `rgba(251,191,36,${0.15 + edgeIntensity * 0.55})`;
        ctx.lineWidth = 1 + edgeIntensity * 2;
      } else if (isBridge) {
        // Bridge link between clusters — cyan/blue
        ctx.strokeStyle = 'rgba(34,211,238,0.45)';
        ctx.lineWidth = 2;
      } else {
        // Normal intra-cluster link — dim gray
        const alpha = 0.08 + l.quality * 0.15;
        ctx.strokeStyle = `rgba(100,116,139,${alpha})`;
        ctx.lineWidth = 0.5 + l.quality;
      }
      ctx.stroke();
    }

    // Draw nodes
    const nodeR = Math.max(2, 4 * this.zoom);
    for (const n of this.net.nodes) {
      const [sx, sy] = this.toScreen(n.x, n.y);
      const color = n.battery <= 0 ? '#991b1b' : CLUSTER_COLORS[n.cluster % CLUSTER_COLORS.length];
      const nodeIntensity = this.reachedNodes.get(n.id) || 0;

      // Flash effect (temporary)
      if (this.flashNodes[n.id]) {
        const f = this.flashNodes[n.id];
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 8, 0, PI2);
        ctx.fillStyle = f.color.replace(')', `,${f.alpha * 0.3})`).replace('rgb', 'rgba');
        ctx.fill();
      }

      // Yellow glow for reached nodes (intensity-based)
      if (nodeIntensity > 0.01) {
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 5, 0, PI2);
        ctx.fillStyle = `rgba(251,191,36,${nodeIntensity * 0.25})`;
        ctx.fill();
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 5, 0, PI2);
        ctx.strokeStyle = `rgba(251,191,36,${nodeIntensity * 0.8})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // Node body
      const isOnDelivery = this.deliveryNodes.has(n.id);
      ctx.beginPath(); ctx.arc(sx, sy, nodeR, 0, PI2);
      if (isOnDelivery) {
        ctx.fillStyle = this.deliveryNodeColor || '#4ade80'; // green or purple
      } else if (nodeIntensity > 0.3) {
        ctx.fillStyle = `rgba(251,191,36,${0.5 + nodeIntensity * 0.5})`;
      } else {
        ctx.fillStyle = color;
      }
      ctx.fill();

      // Delivery path node: glow ring (green or purple)
      if (isOnDelivery) {
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 4, 0, PI2);
        ctx.strokeStyle = this.deliveryGlowColor || 'rgba(74,222,128,0.7)';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Border node ring (white, only if not strongly reached and not on delivery path)
      if (n.border && n.battery > 0 && nodeIntensity < 0.3 && !isOnDelivery) {
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 2, 0, PI2);
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // Mobile indicator
      if (n.mobile && n.battery > 0) {
        ctx.beginPath();
        const hx = sx + Math.cos(n.heading) * (nodeR + 5);
        const hy = sy + Math.sin(n.heading) * (nodeR + 5);
        ctx.moveTo(sx, sy); ctx.lineTo(hx, hy);
        ctx.strokeStyle = 'rgba(251,191,36,0.6)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // WalkFlood-upgraded node indicator — purple ring + "WF" label
      if (n.isWalkFlood && n.battery > 0) {
        // Outer purple ring (distinct from cyan clusters)
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 3, 0, PI2);
        ctx.strokeStyle = '#a78bfa'; // purple
        ctx.lineWidth = 2;
        ctx.stroke();
        // Glow
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 6, 0, PI2);
        ctx.strokeStyle = 'rgba(167,139,250,0.3)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // MPR relay node indicator — purple ring (broadcast mode)
      if (n.isMprRelay && n.battery > 0) {
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 3, 0, PI2);
        ctx.strokeStyle = '#a78bfa'; // purple
        ctx.lineWidth = 2.5;
        ctx.stroke();
        // Outer glow
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 6, 0, PI2);
        ctx.strokeStyle = 'rgba(167,139,250,0.35)';
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      // Legacy S5 indicator (backward compat)
      if (n.isS5 && !n.isWalkFlood && n.battery > 0) {
        ctx.beginPath(); ctx.arc(sx, sy, nodeR + 3, 0, PI2);
        ctx.strokeStyle = '#22d3ee';
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }

    // Draw hop-limit rings (Meshtastic max hop visualization)
    this._drawHopLimitRings(ctx);

    // Draw SRC / DST markers for the next message
    this._drawEndpointMarkers(ctx, nodeR);

    // Draw animated packets
    for (const p of this.animPackets) {
      const nFrom = this.net.nodes[p.from], nTo = this.net.nodes[p.to];
      const [fx, fy] = this.toScreen(nFrom.x, nFrom.y);
      const [tx, ty] = this.toScreen(nTo.x, nTo.y);
      const px = fx + (tx - fx) * p.t;
      const py = fy + (ty - fy) * p.t;

      // Glow
      ctx.beginPath(); ctx.arc(px, py, 6, 0, PI2);
      ctx.fillStyle = p.color === '#fb923c' ? 'rgba(251,146,60,0.25)' : 'rgba(34,211,238,0.25)';
      ctx.fill();

      // Particle
      ctx.beginPath(); ctx.arc(px, py, 3, 0, PI2);
      ctx.fillStyle = p.color;
      ctx.shadowColor = p.color;
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;

      // Trail
      const trail = 0.15;
      if (p.t > trail) {
        const t0 = p.t - trail;
        const tx0 = fx + (tx - fx) * t0;
        const ty0 = fy + (ty - fy) * t0;
        ctx.beginPath(); ctx.moveTo(tx0, ty0); ctx.lineTo(px, py);
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 1.5;
        ctx.globalAlpha = 0.4;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }

    // Distance scale bar (bottom-left corner)
    this._drawScaleBar(ctx);

    // Stats overlay
    this._updateStats();
  }

  _drawScaleBar(ctx) {
    if (!this.net) return;
    const pixelsPerMeter = this.scale * this.zoom;

    // Choose a nice round distance for the scale bar
    const maxBarPx = 120; // max bar width in pixels
    const maxBarMeters = maxBarPx / pixelsPerMeter;
    // Find nearest round number: 100m, 200m, 500m, 1km, 2km, 5km, 10km, 20km, 50km
    const steps = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
    let barMeters = steps[0];
    for (const s of steps) {
      if (s <= maxBarMeters) barMeters = s;
      else break;
    }
    const barPx = barMeters * pixelsPerMeter;
    const label = barMeters >= 1000 ? `${barMeters / 1000} km` : `${barMeters} m`;

    // Draw at bottom-left
    const x = 15;
    const y = this.H - 20;

    // Background
    ctx.fillStyle = 'rgba(15,23,42,0.7)';
    ctx.fillRect(x - 4, y - 16, barPx + 8, 24);

    // Bar line with end caps
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y); ctx.lineTo(x + barPx, y);
    ctx.stroke();
    // End caps
    ctx.beginPath();
    ctx.moveTo(x, y - 5); ctx.lineTo(x, y + 5);
    ctx.moveTo(x + barPx, y - 5); ctx.lineTo(x + barPx, y + 5);
    ctx.stroke();

    // Label
    ctx.font = '10px JetBrains Mono';
    ctx.fillStyle = '#cbd5e1';
    ctx.textAlign = 'center';
    ctx.fillText(label, x + barPx / 2, y - 5);
  }

  _drawHopLimitRings(ctx) {
    // Only show on managed flooding side when SRC is selected
    if (this.labelClass !== 'managed') return;
    if (this.markedSrc < 0 || !this.net) return;

    const src = this.net.nodes[this.markedSrc];
    if (!src) return;
    const [sx, sy] = this.toScreen(src.x, src.y);
    const hopRange = this.net.range; // average 1-hop range in world units

    const limits = [
      { hops: 3, color: 'rgba(251,146,60,0.25)', label: 'Hop 3 (min limit)' },
      { hops: 7, color: 'rgba(251,146,60,0.10)', label: 'Hop 7 (max limit)' },
    ];

    for (const lim of limits) {
      const worldRadius = hopRange * lim.hops;
      const screenRadius = worldRadius * this.scale * this.zoom;

      // Dashed ring
      ctx.beginPath();
      ctx.arc(sx, sy, screenRadius, 0, PI2);
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = lim.color;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.setLineDash([]);

      // Label
      ctx.font = '9px JetBrains Mono';
      ctx.fillStyle = lim.color.replace(/[\d.]+\)$/, '0.6)');
      ctx.textAlign = 'left';
      ctx.fillText(lim.label, sx + screenRadius + 4, sy - 4);
    }

    // Shade "unreachable zone" beyond hop 7
    const maxWorldRadius = hopRange * 7;
    const maxScreenRadius = maxWorldRadius * this.scale * this.zoom;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, this.W, this.H);
    ctx.arc(sx, sy, maxScreenRadius, 0, PI2, true); // cut out the reachable area
    ctx.fillStyle = 'rgba(153, 27, 27, 0.08)';
    ctx.fill();
    ctx.restore();
  }

  _drawEndpointMarkers(ctx, nodeR) {
    const markers = [
      { id: this.markedSrc, label: 'SRC', color: '#4ade80', glow: 'rgba(74,222,128,0.3)' },
      { id: this.markedDst, label: 'DST', color: '#f87171', glow: 'rgba(248,113,113,0.3)' },
    ];
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 300); // 0..1 pulsing

    for (const m of markers) {
      if (m.id < 0 || !this.net || !this.net.nodes[m.id]) continue;
      const n = this.net.nodes[m.id];
      const [sx, sy] = this.toScreen(n.x, n.y);
      const outerR = nodeR + 6 + pulse * 4;

      // Pulsing glow ring
      ctx.beginPath(); ctx.arc(sx, sy, outerR, 0, PI2);
      ctx.strokeStyle = m.color;
      ctx.lineWidth = 2;
      ctx.globalAlpha = 0.4 + pulse * 0.4;
      ctx.stroke();
      ctx.globalAlpha = 1;

      // Soft glow fill
      ctx.beginPath(); ctx.arc(sx, sy, outerR + 2, 0, PI2);
      ctx.fillStyle = m.glow;
      ctx.fill();

      // Label above
      ctx.font = 'bold 10px JetBrains Mono';
      ctx.textAlign = 'center';
      ctx.fillStyle = m.color;
      ctx.fillText(m.label, sx, sy - outerR - 4);
    }
  }

  _updateStats() {
    const prefix = this.labelClass === 'managed' ? 'managed' : 'system5';
    document.getElementById(`tx-${prefix}`).textContent = this.stats.tx;
    document.getElementById(`del-${prefix}`).textContent = this.stats.delivered;
    const rate = this.stats.sent > 0 ? (this.stats.delivered / this.stats.sent * 100).toFixed(1) + '%' : '-';
    document.getElementById(`rate-${prefix}`).textContent = rate;
    const avgHops = this.stats.delivered > 0 ? (this.stats.totalHops / this.stats.delivered).toFixed(1) : '-';
    document.getElementById(`hops-${prefix}`).textContent =
      this.stats.lastHops > 0 ? `${this.stats.lastHops} (avg ${avgHops})` : avgHops;
  }
}

