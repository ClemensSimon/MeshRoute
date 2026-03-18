/**
 * Simulation Results Visualization
 * Loads results.json and renders interactive charts using Canvas.
 */

(function () {
  'use strict';

  const COLORS = {
    cyan: '#22d3ee',
    red: '#f87171',
    green: '#4ade80',
    yellow: '#fbbf24',
    orange: '#fb923c',
    purple: '#a855f7',
    teal: '#14b8a6',
    text: '#e2e8f0',
    textMuted: '#94a3b8',
    bg: '#0f172a',
    bgCard: '#1e293b',
    border: '#334155',
  };

  const QOS_LABELS = [
    'SOS/Emergency', 'Critical Alert', 'High Priority', 'Standard',
    'Bulk Data', 'Diagnostics', 'Firmware OTA', 'Background'
  ];

  let resultsData = null;

  // ── Load data ──
  async function loadResults() {
    try {
      const resp = await fetch('simulator/results.json');
      if (!resp.ok) throw new Error(resp.statusText);
      resultsData = await resp.json();
      renderAll();
    } catch (e) {
      console.warn('Could not load simulation results:', e);
      const section = document.getElementById('sim-results');
      if (section) section.style.display = 'none';
    }
  }

  function renderAll() {
    if (!resultsData || !resultsData.length) return;
    renderSummaryTable();
    renderBWChart();
    renderDeliveryChart();
    renderLoadChart();
    renderQoSChart();
  }

  // ── Summary Table ──
  function renderSummaryTable() {
    const tbody = document.getElementById('sim-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    for (const r of resultsData) {
      const bw = r.comparison ? r.comparison.bw_savings_pct : 0;
      const s5 = r.system5;
      const fl = r.flooding;
      const fallback = s5.fallback_used || 0;
      const switches = s5.route_switches || 0;

      const tr = document.createElement('tr');
      tr.className = r.category === 'stress' ? 'stress-row' : '';
      tr.innerHTML = `
        <td>${r.name}</td>
        <td>${r.config.n_nodes}</td>
        <td class="flood-val">${fl.total_tx.toLocaleString()}</td>
        <td class="sys5-val">${s5.total_tx.toLocaleString()}</td>
        <td class="sys5-val">${s5.delivery_rate}%</td>
        <td>${bw}%</td>
        <td>${s5.avg_hops}</td>
        <td title="Fallbacks: ${fallback}, Route switches: ${switches}">${s5.max_node_load}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  // ── Canvas helpers ──
  function getCtx(id, dpr) {
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    dpr = dpr || window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.w = rect.width;
    ctx.h = rect.height;
    return ctx;
  }

  // ── Bandwidth Comparison Bar Chart ──
  function renderBWChart() {
    const ctx = getCtx('chart-bandwidth');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 60, left: 70 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    const data = resultsData;
    const n = data.length;
    const barW = Math.min(chartW / n * 0.35, 30);
    const gap = chartW / n;

    // Find max TX (use log scale)
    const allTX = data.flatMap(d => [d.flooding.total_tx, d.system5.total_tx]);
    const maxLog = Math.ceil(Math.log10(Math.max(...allTX)));

    // Grid lines
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 0.5;
    ctx.font = '10px monospace';
    ctx.fillStyle = COLORS.textMuted;
    ctx.textAlign = 'right';
    for (let i = 0; i <= maxLog; i++) {
      const y = pad.top + chartH - (i / maxLog) * chartH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
      const label = i === 0 ? '1' : '10' + superscript(i);
      ctx.fillText(Math.pow(10, i).toLocaleString(), pad.left - 5, y + 3);
    }

    // Bars
    for (let i = 0; i < n; i++) {
      const d = data[i];
      const cx = pad.left + gap * i + gap / 2;

      // Flooding bar
      const floodH = (Math.log10(Math.max(d.flooding.total_tx, 1)) / maxLog) * chartH;
      ctx.fillStyle = COLORS.red;
      ctx.globalAlpha = 0.8;
      ctx.fillRect(cx - barW - 1, pad.top + chartH - floodH, barW, floodH);

      // System 5 bar
      const s5H = (Math.log10(Math.max(d.system5.total_tx, 1)) / maxLog) * chartH;
      ctx.fillStyle = COLORS.cyan;
      ctx.fillRect(cx + 1, pad.top + chartH - s5H, barW, s5H);

      ctx.globalAlpha = 1;

      // Label
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      ctx.save();
      ctx.translate(cx, pad.top + chartH + 8);
      ctx.rotate(-0.5);
      const shortName = d.name.replace(/\(.+\)/, '').trim();
      ctx.fillText(shortName.substring(0, 18), 0, 0);
      ctx.restore();
    }

    // Legend
    ctx.globalAlpha = 1;
    ctx.font = '11px monospace';
    const lx = pad.left + 10;
    ctx.fillStyle = COLORS.red;
    ctx.fillRect(lx, 8, 12, 12);
    ctx.fillStyle = COLORS.text;
    ctx.textAlign = 'left';
    ctx.fillText('Flooding', lx + 16, 18);
    ctx.fillStyle = COLORS.cyan;
    ctx.fillRect(lx + 90, 8, 12, 12);
    ctx.fillStyle = COLORS.text;
    ctx.fillText('System 5', lx + 106, 18);

    // Y axis label
    ctx.save();
    ctx.fillStyle = COLORS.textMuted;
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';
    ctx.translate(12, pad.top + chartH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Total Transmissions (log scale)', 0, 0);
    ctx.restore();
  }

  function superscript(n) {
    const sups = '\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079';
    return String(n).split('').map(c => sups[parseInt(c)]).join('');
  }

  // ── Delivery Rate Chart ──
  function renderDeliveryChart() {
    const ctx = getCtx('chart-delivery');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 60, left: 50 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    const data = resultsData;
    const n = data.length;

    // Grid
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 0.5;
    ctx.font = '10px monospace';
    ctx.fillStyle = COLORS.textMuted;
    ctx.textAlign = 'right';
    for (let pct = 0; pct <= 100; pct += 20) {
      const y = pad.top + chartH - (pct / 100) * chartH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(W - pad.right, y);
      ctx.stroke();
      ctx.fillText(pct + '%', pad.left - 5, y + 3);
    }

    const gap = chartW / n;

    // Flooding line
    ctx.beginPath();
    ctx.strokeStyle = COLORS.red;
    ctx.lineWidth = 2;
    for (let i = 0; i < n; i++) {
      const x = pad.left + gap * i + gap / 2;
      const y = pad.top + chartH - (data[i].flooding.delivery_rate / 100) * chartH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // System 5 line
    ctx.beginPath();
    ctx.strokeStyle = COLORS.cyan;
    ctx.lineWidth = 2;
    for (let i = 0; i < n; i++) {
      const x = pad.left + gap * i + gap / 2;
      const y = pad.top + chartH - (data[i].system5.delivery_rate / 100) * chartH;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Points + values
    for (let i = 0; i < n; i++) {
      const x = pad.left + gap * i + gap / 2;

      // Flooding dot
      const yf = pad.top + chartH - (data[i].flooding.delivery_rate / 100) * chartH;
      ctx.beginPath();
      ctx.arc(x, yf, 4, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.red;
      ctx.fill();

      // System 5 dot
      const ys = pad.top + chartH - (data[i].system5.delivery_rate / 100) * chartH;
      ctx.beginPath();
      ctx.arc(x, ys, 5, 0, Math.PI * 2);
      ctx.fillStyle = COLORS.cyan;
      ctx.fill();

      // Value label for System 5
      ctx.fillStyle = COLORS.cyan;
      ctx.font = '10px monospace';
      ctx.textAlign = 'center';
      ctx.fillText(data[i].system5.delivery_rate + '%', x, ys - 10);

      // X label
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '9px monospace';
      ctx.save();
      ctx.translate(x, pad.top + chartH + 8);
      ctx.rotate(-0.5);
      const shortName = data[i].name.replace(/\(.+\)/, '').trim();
      ctx.fillText(shortName.substring(0, 18), 0, 0);
      ctx.restore();
    }

    // Legend
    ctx.font = '11px monospace';
    const lx = pad.left + 10;
    ctx.fillStyle = COLORS.red;
    ctx.fillRect(lx, 8, 12, 3);
    ctx.fillStyle = COLORS.text;
    ctx.textAlign = 'left';
    ctx.fillText('Flooding', lx + 16, 14);
    ctx.fillStyle = COLORS.cyan;
    ctx.fillRect(lx + 90, 8, 12, 3);
    ctx.fillStyle = COLORS.text;
    ctx.fillText('System 5', lx + 106, 14);
  }

  // ── Node Load Distribution ──
  function renderLoadChart() {
    const ctx = getCtx('chart-load');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 20, left: 10 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    // Pick two scenarios: one normal, one stress
    const normal = resultsData.find(r => r.category === 'scale' && r.config.n_nodes >= 100);
    const stress = resultsData.find(r => r.category === 'stress');
    if (!normal) return;

    const scenarios = stress ? [normal, stress] : [normal];
    const colW = chartW / scenarios.length;

    ctx.font = '11px monospace';
    ctx.textAlign = 'center';

    for (let s = 0; s < scenarios.length; s++) {
      const r = scenarios[s];
      const ox = pad.left + s * colW;
      const halfW = colW / 2;

      // Title
      ctx.fillStyle = COLORS.text;
      ctx.fillText(r.name.substring(0, 25), ox + halfW, 16);

      // Flooding distribution
      const floodDist = r.flooding.load_distribution || [];
      const s5Dist = r.system5.load_distribution || [];
      const maxBucket = Math.max(...floodDist, ...s5Dist, 1);

      const bucketW = (colW - 20) / 10;
      const barMaxH = chartH - 20;

      for (let b = 0; b < 10; b++) {
        const bx = ox + 10 + b * bucketW;

        // Flood bar
        const fh = floodDist[b] ? (floodDist[b] / maxBucket) * barMaxH : 0;
        ctx.fillStyle = COLORS.red;
        ctx.globalAlpha = 0.5;
        ctx.fillRect(bx, pad.top + barMaxH - fh + 10, bucketW * 0.45, fh);

        // S5 bar
        const sh = s5Dist[b] ? (s5Dist[b] / maxBucket) * barMaxH : 0;
        ctx.fillStyle = COLORS.cyan;
        ctx.globalAlpha = 0.7;
        ctx.fillRect(bx + bucketW * 0.5, pad.top + barMaxH - sh + 10, bucketW * 0.45, sh);
      }
      ctx.globalAlpha = 1;

      // X labels
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '8px monospace';
      ctx.fillText('low', ox + 20, H - 4);
      ctx.fillText('high', ox + colW - 20, H - 4);
    }
  }

  // ── QoS Breakdown ──
  function renderQoSChart() {
    const ctx = getCtx('chart-qos');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 10, right: 10, bottom: 10, left: 10 };

    ctx.clearRect(0, 0, W, H);

    // Find a stress scenario with QoS data
    const stressScenario = resultsData.find(
      r => r.category === 'stress' && r.system5.qos_breakdown
    );
    if (!stressScenario || !stressScenario.system5.qos_breakdown) {
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '12px monospace';
      ctx.textAlign = 'center';
      ctx.fillText('No QoS data available', W / 2, H / 2);
      return;
    }

    const qos = stressScenario.system5.qos_breakdown;
    const priorities = Object.keys(qos).sort((a, b) => parseInt(a) - parseInt(b));

    const barH = Math.min((H - pad.top - pad.bottom) / 8 - 4, 24);
    const labelW = 110;
    const barMaxW = W - pad.left - pad.right - labelW - 60;

    ctx.font = '10px monospace';
    ctx.textAlign = 'left';

    for (let i = 0; i < 8; i++) {
      const key = String(i);
      const y = pad.top + i * (barH + 4);
      const d = qos[key];

      // Label
      ctx.fillStyle = i <= 1 ? COLORS.green : i <= 3 ? COLORS.cyan : COLORS.textMuted;
      const label = `P${i} ${QOS_LABELS[i] || ''}`;
      ctx.fillText(label.substring(0, 16), pad.left, y + barH * 0.7);

      if (!d) {
        ctx.fillStyle = COLORS.border;
        ctx.fillRect(pad.left + labelW, y, barMaxW, barH);
        ctx.fillStyle = COLORS.textMuted;
        ctx.fillText('no data', pad.left + labelW + 5, y + barH * 0.7);
        continue;
      }

      // Background (sent)
      ctx.fillStyle = COLORS.border;
      ctx.fillRect(pad.left + labelW, y, barMaxW, barH);

      // Delivered portion
      const deliveredW = (d.delivered / d.sent) * barMaxW;
      const hue = d.rate >= 90 ? COLORS.green : d.rate >= 60 ? COLORS.yellow : COLORS.red;
      ctx.fillStyle = hue;
      ctx.globalAlpha = 0.8;
      ctx.fillRect(pad.left + labelW, y, deliveredW, barH);
      ctx.globalAlpha = 1;

      // Rate text
      ctx.fillStyle = COLORS.text;
      ctx.textAlign = 'left';
      ctx.fillText(`${d.rate}%  (${d.delivered}/${d.sent})`,
        pad.left + labelW + barMaxW + 5, y + barH * 0.7);
    }

    // Scenario label
    ctx.fillStyle = COLORS.textMuted;
    ctx.font = '9px monospace';
    ctx.textAlign = 'right';
    ctx.fillText(stressScenario.name, W - pad.right, H - 2);
  }

  // ── Init ──
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadResults);
  } else {
    loadResults();
  }

  // Redraw on resize
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (resultsData) renderAll();
    }, 200);
  });
})();
