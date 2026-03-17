// Hero canvas — ambient mesh background animation
(function heroAnim() {
  const c = document.getElementById('hero-canvas');
  const ctx = c.getContext('2d');
  let W, H, nodes = [];
  function resize() {
    W = c.width = c.offsetWidth;
    H = c.height = c.offsetHeight;
    nodes = [];
    const count = Math.floor((W * H) / 18000);
    for (let i = 0; i < count; i++)
      nodes.push({ x: rand(0, W), y: rand(0, H), vx: rand(-0.3, 0.3), vy: rand(-0.3, 0.3), r: rand(1.5, 3) });
  }
  resize();
  window.addEventListener('resize', resize);

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      n.x += n.vx; n.y += n.vy;
      if (n.x < 0 || n.x > W) n.vx *= -1;
      if (n.y < 0 || n.y > H) n.vy *= -1;
      for (let j = i + 1; j < nodes.length; j++) {
        const d = dist(n, nodes[j]);
        if (d < 150) {
          ctx.beginPath();
          ctx.moveTo(n.x, n.y);
          ctx.lineTo(nodes[j].x, nodes[j].y);
          ctx.strokeStyle = `rgba(34,211,238,${0.15 * (1 - d / 150)})`;
          ctx.lineWidth = 0.6;
          ctx.stroke();
        }
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, PI2);
      ctx.fillStyle = 'rgba(34,211,238,0.5)';
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  draw();
})();
