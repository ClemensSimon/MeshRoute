// Generic mesh simulation engine for algorithm visualizations
class MeshSim {
  constructor(canvasId, opts) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.opts = opts;
    this.nodes = [];
    this.edges = [];
    this.packets = [];
    this.time = 0;
    this.running = false;
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }
  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    this.W = this.canvas.width = r.width;
    this.H = this.canvas.height = r.height;
    if (this.opts.setup) this.opts.setup(this);
  }
  addNode(x, y, props = {}) {
    const n = { id: this.nodes.length, x: x * this.W, y: y * this.H, ...props };
    this.nodes.push(n);
    return n;
  }
  addEdge(a, b, props = {}) {
    this.edges.push({ a, b, ...props });
  }
  hasEdge(a, b) {
    return this.edges.some(e => (e.a === a && e.b === b) || (e.a === b && e.b === a));
  }
  sendPacket(fromId, toId, color, speed = 0.012, onArrive) {
    this.packets.push({
      from: fromId, to: toId, t: 0, color, speed,
      onArrive: onArrive || null
    });
  }
  start() {
    if (this.running) return;
    this.running = true;
    this._loop();
  }
  _loop() {
    if (!this.running) return;
    this.time += 0.016;
    this.update();
    this.draw();
    requestAnimationFrame(() => this._loop());
  }
  update() {
    for (let i = this.packets.length - 1; i >= 0; i--) {
      const p = this.packets[i];
      p.t += p.speed;
      if (p.t >= 1) {
        if (p.onArrive) p.onArrive(p.to);
        this.packets.splice(i, 1);
      }
    }
    if (this.opts.tick) this.opts.tick(this);
  }
  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);

    // edges
    for (const e of this.edges) {
      const a = this.nodes[e.a], b = this.nodes[e.b];
      ctx.beginPath();
      ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = e.color || 'rgba(100,116,139,0.25)';
      ctx.lineWidth = e.width || 1;
      ctx.stroke();
    }

    // cluster regions
    if (this.opts.drawClusters) this.opts.drawClusters(this, ctx);

    // packets
    for (const p of this.packets) {
      const a = this.nodes[p.from], b = this.nodes[p.to];
      const x = lerp(a.x, b.x, p.t);
      const y = lerp(a.y, b.y, p.t);
      ctx.beginPath();
      ctx.arc(x, y, p.size || 4, 0, PI2);
      ctx.fillStyle = p.color;
      ctx.shadowColor = p.color;
      ctx.shadowBlur = 10;
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    // nodes
    for (const n of this.nodes) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r || 6, 0, PI2);
      ctx.fillStyle = n.color || '#334155';
      ctx.fill();
      ctx.strokeStyle = n.stroke || 'rgba(148,163,184,0.4)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      if (n.label) {
        ctx.fillStyle = '#94a3b8';
        ctx.font = '10px JetBrains Mono';
        ctx.textAlign = 'center';
        ctx.fillText(n.label, n.x, n.y - (n.r || 6) - 5);
      }
    }

    if (this.opts.drawOverlay) this.opts.drawOverlay(this, ctx);
  }
}
