// MeshRoute Simulator - Conversion Scenario
// Shows progressive migration from 100% Legacy to 90% S5 on the same network.
// Each phase upgrades more nodes and re-routes the same message.

const CONVERSION_PHASES = [
  { ratio: 0,    label: '0% S5 — Pure Legacy',     desc: 'All nodes use managed flooding. This is today\'s Meshtastic. Every node rebroadcasts to all neighbors.' },
  { ratio: 0.10, label: '10% S5 — Early Adopters',  desc: 'A few nodes upgraded. S5 islands form but can\'t connect to each other. Hybrid flooding may cost MORE than pure flooding — this is the migration tax.' },
  { ratio: 0.30, label: '30% S5 — Islands Growing', desc: 'S5 islands grow but rarely connect end-to-end. Hybrid mode adds overhead until a full S5 path exists.' },
  { ratio: 0.50, label: '50% S5 — Approaching Critical Mass',   desc: 'Half the network is S5. If a full S5 path exists, TX drops dramatically. If not, hybrid overhead persists.' },
  { ratio: 0.70, label: '70% S5 — Near Tipping Point', desc: 'Most nodes are S5. The probability of a full S5 path is high. When it connects: 1 TX per hop.' },
  { ratio: 0.90, label: '90% S5 — Directed Routing',  desc: 'Full S5 paths almost always exist. Directed routing delivers in ~5 TX what flooding needs ~800 for.' },
];

let conversionState = null;

function isConversionScenario(scenarioKey) {
  const cfg = SCENARIOS[scenarioKey];
  return cfg && cfg.conversion === true;
}

function initConversion() {
  conversionState = {
    phase: -1,
    migrationDone: false,
    results: [],       // { ratio, mfTx, dualTx, mfDelivered, dualDelivered, mode }
    baseS5Order: null,  // fixed node upgrade order (consistent across phases)
  };
  return conversionState;
}

function conversionPhaseCount() {
  return CONVERSION_PHASES.length;
}

function isConversionMigrationDone() {
  return conversionState && conversionState.migrationDone;
}

function getConversionPhaseIndex() {
  return conversionState ? conversionState.phase : -1;
}

// Pre-compute a fixed upgrade order (nodes sorted by strategic value)
function computeUpgradeOrder(net) {
  const alive = net.nodes.filter(n => n.battery > 0);
  // Prioritize: border nodes first, then by neighbor count (high connectivity)
  alive.sort((a, b) => {
    if (a.border !== b.border) return b.border ? 1 : -1;
    return Object.keys(b.neighbors).length - Object.keys(a.neighbors).length;
  });
  return alive.map(n => n.id);
}

// Advance to next conversion phase — upgrades nodes and simulates routing
function advanceConversionPhase(net, src, dst) {
  if (!conversionState) return null;
  conversionState.phase++;
  const p = conversionState.phase;
  if (p >= CONVERSION_PHASES.length) return null;

  const phaseInfo = CONVERSION_PHASES[p];

  // Compute upgrade order once (first call)
  if (!conversionState.baseS5Order) {
    conversionState.baseS5Order = computeUpgradeOrder(net);
  }

  // Reset all S5 flags
  for (const n of net.nodes) n.isS5 = false;

  // Upgrade nodes according to ratio
  const nUpgrade = Math.floor(conversionState.baseS5Order.length * phaseInfo.ratio);
  const upgradedIds = new Set();
  for (let i = 0; i < nUpgrade; i++) {
    const nid = conversionState.baseS5Order[i];
    net.nodes[nid].isS5 = true;
    upgradedIds.add(nid);
  }

  // Simulate managed flooding (always the same — baseline)
  const mfResult = simulateManagedFlood(net, src, dst, new RNG(42));

  // Simulate dual-mode with current S5 ratio
  let dualResult;
  if (phaseInfo.ratio === 0) {
    // 0% S5 = pure managed flooding (same as left panel)
    dualResult = simulateManagedFlood(net, src, dst, new RNG(42));
    dualResult.mode = 'flood';
  } else {
    dualResult = simulateDualMode(net, src, dst, new RNG(42));
  }

  // Group dual-mode events by hop for animation
  const dualHopGroups = {};
  let dualMaxHop = 0;
  for (const ev of dualResult.txEvents) {
    const h = ev.hop || 0;
    if (!dualHopGroups[h]) dualHopGroups[h] = [];
    dualHopGroups[h].push(ev);
    if (h > dualMaxHop) dualMaxHop = h;
  }

  const result = {
    phaseInfo,
    phaseIndex: p,
    ratio: phaseInfo.ratio,
    nUpgraded: nUpgrade,
    upgradedIds,
    mfResult,
    dualResult,
    dualHopGroups,
    dualMaxHop,
    s5TxCount: dualResult.txEvents.filter(e => e.mode === 's5').length,
    floodTxCount: dualResult.txEvents.filter(e => e.mode !== 's5').length,
  };

  conversionState.results.push({
    ratio: phaseInfo.ratio,
    mfTx: mfResult.totalTx,
    dualTx: dualResult.totalTx,
    mfDelivered: mfResult.delivered,
    dualDelivered: dualResult.delivered,
    mode: dualResult.mode || 'mixed',
  });

  if (p >= CONVERSION_PHASES.length - 1) {
    conversionState.migrationDone = true;
  }

  return result;
}

// Build the summary table after all phases
function buildConversionSummaryHtml() {
  if (!conversionState) return '';
  const results = conversionState.results;
  let rows = '';
  for (const r of results) {
    const pct = Math.round(r.ratio * 100);
    const savings = r.mfTx > 0 ? ((1 - r.dualTx / r.mfTx) * 100).toFixed(0) : '0';
    const savingsClass = +savings > 0 ? 'log-good' : 'log-dim';
    rows += `<tr>
      <td>${pct}%</td>
      <td>${r.mfTx}</td>
      <td>${r.dualTx}</td>
      <td class="${savingsClass}">${savings}%</td>
      <td>${r.dualDelivered ? '<span class="log-good">Yes</span>' : '<span class="log-bad">No</span>'}</td>
    </tr>`;
  }
  return `<table style="width:100%;border-collapse:collapse;font-size:0.75rem;">
    <tr style="border-bottom:1px solid var(--border);color:var(--text-dim);">
      <th style="text-align:left;padding:0.3rem 0.5rem;">S5 %</th>
      <th style="text-align:right;padding:0.3rem 0.5rem;">Flood TX</th>
      <th style="text-align:right;padding:0.3rem 0.5rem;">Dual TX</th>
      <th style="text-align:right;padding:0.3rem 0.5rem;">Saved</th>
      <th style="text-align:center;padding:0.3rem 0.5rem;">Del?</th>
    </tr>
    ${rows}
  </table>`;
}
