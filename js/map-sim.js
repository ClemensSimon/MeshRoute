// Map simulation engine for scale scenarios (Local, Europe, Global)
class MapSim {
  constructor(canvasId, panelId, opts) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.panelContent = document.getElementById(panelId);
    this.opts = opts;
    this.nodes = [];
    this.links = [];
    this.packets = [];
    this.logEntries = [];
    this.time = 0;
    this.W = 0; this.H = 0;
    this.resize();
    window.addEventListener('resize', () => this.resize());
    // start when visible
    const obs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) { this.start(); obs.disconnect(); }
    }, { threshold: 0.1 });
    obs.observe(this.canvas);
  }
  resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    this.W = this.canvas.width = r.width;
    this.H = this.canvas.height = r.height;
    this.nodes = []; this.links = []; this.packets = [];
    if (this.opts.setup) this.opts.setup(this);
  }
  addNode(x, y, props = {}) {
    const n = { id: this.nodes.length, x: x * this.W, y: y * this.H, queue: 0, battery: rand(60,100), fwdCount: 0, rxCount: 0, ...props };
    this.nodes.push(n);
    return n;
  }
  addLink(a, b, props = {}) {
    this.links.push({ a, b, active: false, ...props });
  }
  hasLink(a, b) {
    return this.links.some(l => (l.a === a && l.b === b) || (l.a === b && l.b === a));
  }
  sendPacket(fromId, toId, color, speed, size, onArrive) {
    this.packets.push({ from: fromId, to: toId, t: 0, color, speed: speed || 0.015, size: size || 4, onArrive });
  }
  log(msg, color) {
    this.logEntries.unshift({ msg, color: color || 'var(--text-dim)', time: this.time });
    if (this.logEntries.length > 14) this.logEntries.pop();
    this._renderLog();
  }
  _renderLog() {
    if (!this.panelContent) return;
    let html = '';
    this.logEntries.forEach(e => {
      const t = e.time.toFixed(1) + 's';
      html += `<div class="np-log-entry"><span class="np-time">[${t}]</span><span style="color:${e.color}">${e.msg}</span></div>`;
    });
    this.panelContent.innerHTML = html;
  }
  start() {
    if (this._running) return;
    this._running = true;
    this._loop();
  }
  _loop() {
    if (!this._running) return;
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
    if (this.opts.drawMap) this.opts.drawMap(this, ctx);
    for (const l of this.links) {
      const a = this.nodes[l.a], b = this.nodes[l.b];
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = l.active ? (l.activeColor || 'rgba(34,211,238,0.5)') : (l.color || 'rgba(100,116,139,0.15)');
      ctx.lineWidth = l.active ? (l.activeWidth || 2) : (l.width || 0.8);
      if (l.dashed) ctx.setLineDash([6, 4]); else ctx.setLineDash([]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    if (this.opts.drawOverlay) this.opts.drawOverlay(this, ctx);
    for (const p of this.packets) {
      const a = this.nodes[p.from], b = this.nodes[p.to];
      const x = lerp(a.x, b.x, p.t), y = lerp(a.y, b.y, p.t);
      ctx.beginPath(); ctx.arc(x, y, p.size, 0, PI2);
      ctx.fillStyle = p.color;
      ctx.shadowColor = p.color; ctx.shadowBlur = 12;
      ctx.fill(); ctx.shadowBlur = 0;
      const tx = lerp(a.x, b.x, Math.max(0, p.t - 0.12));
      const ty = lerp(a.y, b.y, Math.max(0, p.t - 0.12));
      const grad = ctx.createLinearGradient(tx, ty, x, y);
      grad.addColorStop(0, 'transparent'); grad.addColorStop(1, p.color);
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y);
      ctx.strokeStyle = grad; ctx.lineWidth = p.size * 0.8; ctx.stroke();
    }
    for (const n of this.nodes) {
      if (n.active) {
        ctx.beginPath(); ctx.arc(n.x, n.y, (n.r || 6) + 6, 0, PI2);
        ctx.fillStyle = (n.glowColor || 'rgba(34,211,238,0.12)'); ctx.fill();
      }
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r || 6, 0, PI2);
      ctx.fillStyle = n.color || '#334155'; ctx.fill();
      ctx.strokeStyle = n.stroke || 'rgba(148,163,184,0.3)';
      ctx.lineWidth = 1.5; ctx.stroke();
      if (n.label) {
        ctx.font = `${n.fontSize || 9}px JetBrains Mono`;
        ctx.fillStyle = n.labelColor || '#94a3b8';
        ctx.textAlign = 'center';
        ctx.fillText(n.label, n.x, n.y - (n.r || 6) - 6);
      }
      if (n.queue > 0) {
        const bw = 22, bh = 3;
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 4, bw, bh);
        ctx.fillStyle = n.queue > 5 ? '#f87171' : n.queue > 2 ? '#fbbf24' : '#4ade80';
        ctx.fillRect(n.x - bw/2, n.y + (n.r||6) + 4, bw * Math.min(1, n.queue/8), bh);
      }
      if (n.showBattery) {
        const bx = n.x + (n.r||6) + 4, by = n.y - 4;
        ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.fillRect(bx, by, 3, 8);
        ctx.fillStyle = n.battery > 50 ? '#4ade80' : n.battery > 20 ? '#fbbf24' : '#f87171';
        const bFill = 8 * (n.battery / 100);
        ctx.fillRect(bx, by + 8 - bFill, 3, bFill);
      }
    }
  }
}
