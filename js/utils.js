// Shared utility functions
const PI2 = Math.PI * 2;
function lerp(a, b, t) { return a + (b - a) * t; }
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
function rand(min, max) { return Math.random() * (max - min) + min; }

// BFS pathfinder — finds shortest path using actual links
// Returns array of node IDs or empty array if no path
function bfsPath(links, from, to, exclude) {
  const adj = {};
  links.forEach(l => {
    if (!adj[l.a]) adj[l.a] = [];
    if (!adj[l.b]) adj[l.b] = [];
    adj[l.a].push(l.b);
    adj[l.b].push(l.a);
  });
  const visited = new Set(exclude || []);
  visited.delete(from); visited.delete(to);
  const queue = [[from]];
  visited.add(from);
  while (queue.length > 0) {
    const path = queue.shift();
    const node = path[path.length - 1];
    if (node === to) return path;
    for (const nb of (adj[node] || [])) {
      if (!visited.has(nb)) {
        visited.add(nb);
        queue.push([...path, nb]);
      }
    }
  }
  return [];
}

// Find K shortest disjoint-ish paths (avoiding interior nodes of previous paths)
function findKPaths(links, from, to, k) {
  const paths = [];
  const usedInterior = new Set();
  for (let i = 0; i < k; i++) {
    const p = bfsPath(links, from, to, usedInterior);
    if (p.length === 0) break;
    paths.push(p);
    // mark interior nodes (not src/dst) as used to force different paths
    for (let j = 1; j < p.length - 1; j++) usedInterior.add(p[j]);
  }
  return paths;
}
