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
  let bwLogScale = true; // toggle between log and linear

  // ── Load data ──
  async function loadResults() {
    try {
      const resp = await fetch('simulator/results.json');
      if (!resp.ok) throw new Error(resp.statusText);
      resultsData = await resp.json();
      setupScaleToggle();
      renderAll();
    } catch (e) {
      console.warn('Could not load simulation results:', e);
      const section = document.getElementById('sim-results');
      if (section) section.style.display = 'none';
    }
  }

  function setupScaleToggle() {
    const btn = document.getElementById('btn-scale-toggle');
    if (!btn) return;
    btn.addEventListener('click', () => {
      bwLogScale = !bwLogScale;
      btn.textContent = bwLogScale ? 'Switch to Linear' : 'Switch to Log';
      renderBWChart();
    });
  }

  function renderAll() {
    if (!resultsData || !resultsData.length) return;
    renderSummaryTable();
    renderBWChart();
    renderDeliveryChart();
    renderLoadChart();
    renderQoSChart();
  }

  // ── Short scenario names for x-axis ──
  function shortName(name) {
    return name
      .replace('Small Local Mesh', 'Small\n20 nodes')
      .replace('Medium City Mesh', 'City\n100 nodes')
      .replace('Large Regional Mesh', 'Regional\n500 nodes')
      .replace('Stress Test (30% degraded links)', 'Stress\n30% deg.')
      .replace('Stress Test (50% degraded links)', 'Stress\n50% deg.')
      .replace('Node Failure (20% killed)', 'Failure\n20% killed')
      .replace('Combined Stress (30% links + 10% nodes)', 'Combined\n30%+10%')
      .replace('Dense Urban (high connectivity)', 'Dense\n200 nodes')
      .replace(/Large Scale \((\d+) nodes.*\)/, 'Large\n$1 nodes')
      .replace(/Metro Scale \((\d+) nodes.*\)/, 'Metro\n$1 nodes')
      .replace(/Extended Range \((\d+) nodes.*\)/, 'Extended\n$1 nodes');
  }

  // ── Scenario descriptions for tooltips ──
  function scenarioTooltip(r) {
    const c = r.config;
    const net = r.network;
    let desc = `${c.n_nodes} nodes over ${(c.area_size/1000).toFixed(0)}km area, LoRa range ${(c.lora_range/1000).toFixed(0)}km\n`;
    desc += `${net.links} links, ${net.clusters} clusters, avg ${net.avg_neighbors} neighbors\n`;
    desc += `${net.avg_routes_per_dest} routes per destination\n`;
    if (c.link_degradation > 0) desc += `${(c.link_degradation*100).toFixed(0)}% of links randomly degraded (quality reduced to 10-50%)\n`;
    if (c.node_kill_fraction > 0) desc += `${(c.node_kill_fraction*100).toFixed(0)}% of nodes killed (battery=0, all links down)\n`;
    if (c.link_degradation === 0 && c.node_kill_fraction === 0) desc += `Normal conditions — no failures applied\n`;
    desc += `\nFlooding: ${r.flooding.total_tx.toLocaleString()} TX, ${r.flooding.delivery_rate}% delivery`;
    desc += `\nSystem 5: ${r.system5.total_tx.toLocaleString()} TX, ${r.system5.delivery_rate}% delivery`;
    if (r.system5.fallback_used) desc += `\nFallback flooding used: ${r.system5.fallback_used}x`;
    if (r.system5.route_switches) desc += `\nRoute switches: ${r.system5.route_switches}x`;
    return desc;
  }

  // ── Summary Table ──
  function renderSummaryTable() {
    const tbody = document.getElementById('sim-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    for (const r of resultsData) {
      const bw = r.comparison ? r.comparison.bw_savings_pct : 0;
      const naive = r.naive_flooding || r.flooding || {};
      const managed = r.managed_flooding || r.flooding || {};
      const nh = r.next_hop || {};
      const s5 = r.system5;

      const tr = document.createElement('tr');
      tr.className = r.category === 'stress' ? 'stress-row' : '';
      tr.title = scenarioTooltip(r);
      tr.style.cursor = 'help';
      tr.innerHTML = `
        <td>${r.name}</td>
        <td>${r.config.n_nodes}</td>
        <td class="flood-val">${(naive.total_tx || 0).toLocaleString()}</td>
        <td style="color:var(--yellow)">${(managed.total_tx || 0).toLocaleString()}</td>
        <td style="color:var(--orange)">${nh.total_tx ? nh.total_tx.toLocaleString() : '—'}</td>
        <td class="sys5-val">${s5.total_tx.toLocaleString()}</td>
        <td class="sys5-val">${s5.delivery_rate}%</td>
        <td>${bw}%</td>
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

  // ── Bandwidth Comparison Bar Chart (Log / Linear toggle) ──
  function renderBWChart() {
    const ctx = getCtx('chart-bandwidth');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 70, left: 75 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    const data = resultsData;
    const n = data.length;
    const barW = Math.min(chartW / n * 0.35, 28);
    const gap = chartW / n;

    const allTX = data.flatMap(d => [d.flooding.total_tx, d.system5.total_tx]);
    const maxVal = Math.max(...allTX);
    const maxLog = Math.ceil(Math.log10(maxVal));

    // ── Y-Axis grid ──
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 0.5;
    ctx.font = '10px monospace';
    ctx.fillStyle = COLORS.textMuted;
    ctx.textAlign = 'right';

    if (bwLogScale) {
      // Log scale grid
      for (let i = 0; i <= maxLog; i++) {
        const y = pad.top + chartH - (i / maxLog) * chartH;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(W - pad.right, y);
        ctx.stroke();
        ctx.fillText(fmtNum(Math.pow(10, i)), pad.left - 8, y + 3);
      }
    } else {
      // Linear scale grid
      const steps = 5;
      const stepVal = niceStep(maxVal, steps);
      const niceMax = stepVal * steps;
      for (let i = 0; i <= steps; i++) {
        const val = stepVal * i;
        const y = pad.top + chartH - (val / niceMax) * chartH;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(W - pad.right, y);
        ctx.stroke();
        ctx.fillText(fmtNum(val), pad.left - 8, y + 3);
      }
    }

    // ── Router definitions for bars ──
    const routers = [
      { key: 'naive_flooding', fallback: 'flooding', color: COLORS.red, label: 'Naive' },
      { key: 'managed_flooding', fallback: 'flooding', color: COLORS.yellow, label: 'Managed' },
      { key: 'next_hop', fallback: null, color: COLORS.orange, label: 'Next-Hop' },
      { key: 'system5', fallback: null, color: COLORS.cyan, label: 'System 5' },
    ];
    const nRouters = routers.length;
    const singleBarW = Math.min(chartW / n / (nRouters + 1) * 0.9, 16);

    // ── Bars ──
    for (let i = 0; i < n; i++) {
      const d = data[i];
      const cx = pad.left + gap * i + gap / 2;
      const groupW = singleBarW * nRouters + (nRouters - 1) * 1;
      const startX = cx - groupW / 2;

      for (let ri = 0; ri < nRouters; ri++) {
        const rd = d[routers[ri].key] || (routers[ri].fallback ? d[routers[ri].fallback] : null);
        if (!rd) continue;
        const val = rd.total_tx;

        let barH;
        if (bwLogScale) {
          barH = (Math.log10(Math.max(val, 1)) / maxLog) * chartH;
        } else {
          const steps = 5;
          const stepVal = niceStep(maxVal, steps);
          const niceMax = stepVal * steps;
          barH = (val / niceMax) * chartH;
        }

        ctx.fillStyle = routers[ri].color;
        ctx.globalAlpha = 0.8;
        ctx.fillRect(startX + ri * (singleBarW + 1), pad.top + chartH - barH, singleBarW, barH);
      }
      ctx.globalAlpha = 1;

      // ── X-axis labels ──
      const sn = shortName(d.name);
      const lines = sn.split('\n');
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      for (let l = 0; l < lines.length; l++) {
        ctx.fillText(lines[l], cx, pad.top + chartH + 14 + l * 12);
      }
    }

    // ── Legend ──
    ctx.globalAlpha = 1;
    ctx.font = '10px monospace';
    ctx.textAlign = 'left';
    let lx = pad.left + 5;
    for (const r of routers) {
      ctx.fillStyle = r.color;
      ctx.fillRect(lx, 8, 10, 10);
      ctx.fillStyle = COLORS.text;
      ctx.fillText(r.label, lx + 14, 17);
      lx += ctx.measureText(r.label).width + 24;
    }

    // Scale indicator (top right)
    ctx.fillStyle = COLORS.textMuted;
    ctx.font = '9px monospace';
    ctx.textAlign = 'right';
    ctx.fillText(bwLogScale ? 'LOG SCALE' : 'LINEAR SCALE', W - pad.right, 16);

    // ── Y axis label ──
    ctx.save();
    ctx.fillStyle = COLORS.textMuted;
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';
    ctx.translate(12, pad.top + chartH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Total Transmissions', 0, 0);
    ctx.restore();
  }

  function fmtNum(v) {
    if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + 'K';
    return String(Math.round(v));
  }

  function niceStep(max, steps) {
    const raw = max / steps;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    if (norm <= 1) return mag;
    if (norm <= 2) return 2 * mag;
    if (norm <= 5) return 5 * mag;
    return 10 * mag;
  }

  // ── Delivery Rate Chart ──
  function renderDeliveryChart() {
    const ctx = getCtx('chart-delivery');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 70, left: 50 };
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

      // X-axis labels (multi-line, horizontal)
      const sn = shortName(data[i].name);
      const lines = sn.split('\n');
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '9px monospace';
      for (let l = 0; l < lines.length; l++) {
        ctx.fillText(lines[l], x, pad.top + chartH + 14 + l * 12);
      }
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

  // ── Hottest Node Load — max TX any single node has to handle ──
  function renderLoadChart() {
    const ctx = getCtx('chart-load');
    if (!ctx) return;
    const W = ctx.w, H = ctx.h;
    const pad = { top: 30, right: 20, bottom: 70, left: 65 };
    const chartW = W - pad.left - pad.right;
    const chartH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    const data = resultsData;
    const n = data.length;
    const gap = chartW / n;

    // Routers to show
    const routers = [
      { key: 'managed_flooding', fallback: 'flooding', color: COLORS.yellow, label: 'Managed' },
      { key: 'system5', fallback: null, color: COLORS.cyan, label: 'System 5' },
    ];

    // Get max load values
    const allLoads = [];
    for (const d of data) {
      for (const r of routers) {
        const rd = d[r.key] || (r.fallback ? d[r.fallback] : null);
        if (rd) allLoads.push(rd.max_node_load);
      }
    }
    const maxLoad = Math.max(...allLoads, 1);
    const maxLog = Math.ceil(Math.log10(maxLoad));

    // Y grid (log scale)
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
      ctx.fillText(fmtNum(Math.pow(10, i)), pad.left - 8, y + 3);
    }

    const barW = Math.min(gap * 0.3, 20);

    for (let i = 0; i < n; i++) {
      const d = data[i];
      const cx = pad.left + gap * i + gap / 2;

      for (let ri = 0; ri < routers.length; ri++) {
        const r = routers[ri];
        const rd = d[r.key] || (r.fallback ? d[r.fallback] : null);
        if (!rd) continue;

        const val = rd.max_node_load;
        const barH = (Math.log10(Math.max(val, 1)) / maxLog) * chartH;
        const bx = cx + (ri - 0.5) * (barW + 2);

        ctx.fillStyle = r.color;
        ctx.globalAlpha = 0.8;
        ctx.fillRect(bx - barW / 2, pad.top + chartH - barH, barW, barH);

        // Value on top
        ctx.globalAlpha = 1;
        ctx.fillStyle = r.color;
        ctx.font = '8px monospace';
        ctx.textAlign = 'center';
        if (val < 100) ctx.fillText(String(val), bx, pad.top + chartH - barH - 4);
      }
      ctx.globalAlpha = 1;

      // X label
      const sn = shortName(d.name);
      const lines = sn.split('\n');
      ctx.fillStyle = COLORS.textMuted;
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      for (let l = 0; l < lines.length; l++) {
        ctx.fillText(lines[l], cx, pad.top + chartH + 14 + l * 12);
      }
    }

    // Legend
    ctx.globalAlpha = 1;
    ctx.font = '10px monospace';
    ctx.textAlign = 'left';
    let lx = pad.left + 5;
    for (const r of routers) {
      ctx.fillStyle = r.color;
      ctx.fillRect(lx, 8, 10, 10);
      ctx.fillStyle = COLORS.text;
      ctx.fillText(r.label, lx + 14, 17);
      lx += ctx.measureText(r.label).width + 24;
    }

    // Y label
    ctx.save();
    ctx.fillStyle = COLORS.textMuted;
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';
    ctx.translate(12, pad.top + chartH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Max TX on busiest node (log)', 0, 0);
    ctx.restore();
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
