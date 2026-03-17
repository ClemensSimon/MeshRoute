// UI effects: scroll reveals, bar chart, counters, nav

// Scroll reveal
const reveals = document.querySelectorAll('.reveal');
const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });
reveals.forEach(el => revealObserver.observe(el));

// Nav scroll state
const navbar = document.getElementById('navbar');
window.addEventListener('scroll', () => {
  navbar.classList.toggle('scrolled', window.scrollY > 50);
});

// Mobile nav toggle
const navToggle = document.getElementById('nav-toggle');
const navMenu = document.getElementById('nav-menu');
if (navToggle && navMenu) {
  navToggle.addEventListener('click', () => {
    navMenu.classList.toggle('open');
    navToggle.textContent = navMenu.classList.contains('open') ? '\u2715' : '\u2630';
  });
  // close menu when a link is clicked
  navMenu.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      navMenu.classList.remove('open');
      navToggle.textContent = '\u2630';
    });
  });
}

// Bar chart animation
const barObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      document.querySelectorAll('.bar').forEach(bar => {
        const score = parseFloat(bar.dataset.score);
        setTimeout(() => { bar.style.height = (score / 10 * 250) + 'px'; }, 200);
      });
      barObserver.disconnect();
    }
  });
}, { threshold: 0.3 });
const barChart = document.getElementById('bar-chart');
if (barChart) barObserver.observe(barChart);

// Counter animation
function animateCounter(id, target, duration = 1500) {
  const el = document.getElementById(id);
  if (!el) return;
  const obs = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) {
      const start = performance.now();
      function tick(now) {
        const p = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(target * eased);
        if (p < 1) requestAnimationFrame(tick);
        else el.textContent = target;
      }
      requestAnimationFrame(tick);
      obs.disconnect();
    }
  }, { threshold: 0.5 });
  obs.observe(el);
}

animateCounter('counter-reliability', 98);
animateCounter('counter-reduction', 92);
animateCounter('counter-failover', 0);
animateCounter('counter-overhead', 2);
animateCounter('counter-table', 210);

// Special: counter-score with decimal
const scoreEl = document.getElementById('counter-score');
if (scoreEl) {
  const obs = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting) {
      const start = performance.now();
      function tick(now) {
        const p = Math.min((now - start) / 1500, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        scoreEl.textContent = (8.8 * eased).toFixed(1);
        if (p < 1) requestAnimationFrame(tick);
        else scoreEl.textContent = '8.8';
      }
      requestAnimationFrame(tick);
      obs.disconnect();
    }
  }, { threshold: 0.5 });
  obs.observe(scoreEl);
}
