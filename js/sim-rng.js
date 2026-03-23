// MeshRoute Simulator - Constants & RNG

const CLUSTER_COLORS = ['#4ade80','#22d3ee','#a78bfa','#fb923c','#f472b6','#fbbf24','#34d399','#818cf8'];
const CLUSTER_DIM    = ['#166534','#0e7490','#5b21b6','#c2410c','#9d174d','#92400e','#065f46','#3730a3'];
// Pastel backgrounds for cluster regions (low alpha)
const CLUSTER_PASTEL = [
  'rgba(74, 222, 128, 0.06)',   // green
  'rgba(34, 211, 238, 0.06)',   // cyan
  'rgba(167, 139, 250, 0.06)',  // purple
  'rgba(251, 146, 60, 0.06)',   // orange
  'rgba(244, 114, 182, 0.06)', // pink
  'rgba(251, 191, 36, 0.06)',  // yellow
  'rgba(52, 211, 153, 0.06)',  // teal
  'rgba(129, 140, 248, 0.06)', // indigo
];
const CLUSTER_BORDER_PASTEL = [
  'rgba(74, 222, 128, 0.15)',
  'rgba(34, 211, 238, 0.15)',
  'rgba(167, 139, 250, 0.15)',
  'rgba(251, 146, 60, 0.15)',
  'rgba(244, 114, 182, 0.15)',
  'rgba(251, 191, 36, 0.15)',
  'rgba(52, 211, 153, 0.15)',
  'rgba(129, 140, 248, 0.15)',
];
const PI2 = Math.PI * 2;
const S5_MAX_HOPS = 20; // Safety cap — S5 has no hop limit, but cap for loop prevention


if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
    if (typeof r === 'number') r = [r, r, r, r];
    const [tl, tr, br, bl] = r;
    this.moveTo(x + tl, y);
    this.lineTo(x + w - tr, y);
    this.quadraticCurveTo(x + w, y, x + w, y + tr);
    this.lineTo(x + w, y + h - br);
    this.quadraticCurveTo(x + w, y + h, x + w - br, y + h);
    this.lineTo(x + bl, y + h);
    this.quadraticCurveTo(x, y + h, x, y + h - bl);
    this.lineTo(x, y + tl);
    this.quadraticCurveTo(x, y, x + tl, y);
    this.closePath();
  };
}


class RNG {
  constructor(seed=42) { this.s = seed; }
  next() { this.s = (this.s * 16807 + 0) % 2147483647; return this.s / 2147483647; }
  uniform(a, b) { return a + this.next() * (b - a); }
  gauss(mu, sigma) {
    let u1 = this.next(), u2 = this.next();
    return mu + sigma * Math.sqrt(-2*Math.log(u1||0.001)) * Math.cos(PI2*u2);
  }
  choice(arr) { return arr[Math.floor(this.next() * arr.length)]; }
  sample(arr, n) {
    const copy = [...arr]; const result = [];
    for (let i = 0; i < Math.min(n, copy.length); i++) {
      const j = Math.floor(this.next() * copy.length);
      result.push(copy.splice(j, 1)[0]);
    }
    return result;
  }
}
