/* analytics_dashboard.js — ExportReady Analytics */
'use strict';

// ── CONFIG ────────────────────────────────────────────────────────────────────
const REFRESH_INTERVAL = 15_000; // ms
const API_URL          = '/analytics/api/';

// Chart.js color tokens (match CSS vars)
const C = {
  accent:  '#00d4ff',
  green:   '#00e676',
  red:     '#ff4d6a',
  amber:   '#ffb700',
  purple:  '#b47cff',
  teal:    '#00bcd4',
  grid:    'rgba(255,255,255,0.04)',
  label:   '#5e7a96',
  bg:      '#161e2b',
};

// ── STATE ─────────────────────────────────────────────────────────────────────
let charts       = {};
let refreshTimer = null;
let lastData     = null;

// ── INIT ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupChart_defaults();
  fetchAndRender();
  refreshTimer = setInterval(fetchAndRender, REFRESH_INTERVAL);
  setupServerClock();
});

// ── FETCH ─────────────────────────────────────────────────────────────────────
async function fetchAndRender() {
  try {
    const res  = await fetch(API_URL, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    lastData   = data;
    renderAll(data);
    hideLoader();
  } catch (err) {
    console.error('[Analytics] fetch failed:', err);
  }
}

// ── TABS ──────────────────────────────────────────────────────────────────────
function setupTabs() {
  const tabs   = document.querySelectorAll('.nav-tab');
  const panels = document.querySelectorAll('.tab-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t   => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const target = document.getElementById('panel-' + tab.dataset.tab);
      if (target) target.classList.add('active');
    });
  });
}

// ── CHART DEFAULTS ────────────────────────────────────────────────────────────
function setupChart_defaults() {
  Chart.defaults.color              = C.label;
  Chart.defaults.font.family        = "'JetBrains Mono', monospace";
  Chart.defaults.font.size          = 11;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = '#1c2637';
  Chart.defaults.plugins.tooltip.borderColor     = '#2a3d55';
  Chart.defaults.plugins.tooltip.borderWidth     = 1;
  Chart.defaults.plugins.tooltip.titleColor      = '#e8edf5';
  Chart.defaults.plugins.tooltip.bodyColor       = '#5e7a96';
  Chart.defaults.plugins.tooltip.padding         = 10;
  Chart.defaults.plugins.tooltip.cornerRadius    = 6;
}

// ── RENDER ALL ────────────────────────────────────────────────────────────────
function renderAll(d) {
  renderKPIs(d);
  renderTrendChart(d);
  renderHourlyChart(d);
  renderHeatmap(d);
  renderFormatBars(d);
  renderStrategyRing(d);
  renderRecentJobs(d);
  renderVisitorKPIs(d);
  renderUserKPIs(d);
  renderUserTable(d);
  renderFeedback(d);
  renderOnboarding(d);
}

// ── KPIs ──────────────────────────────────────────────────────────────────────
function renderKPIs(d) {
  setText('kpi-total',     fmt(d.totalJobs));
  setText('kpi-done',      fmt(d.totalDone));
  setText('kpi-errors',    fmt(d.totalErrors));
  setText('kpi-cancelled', fmt(d.totalCancelled));
  setText('kpi-active',    fmt(d.activeJobs));
  setText('kpi-success',   d.successRate + '%');
  setText('kpi-data',      d.dataHuman);
  setText('kpi-queue',     d.activeJobs + ' / ' + d.maxConcurrent);
}

// ── TREND LINE ────────────────────────────────────────────────────────────────
function renderTrendChart(d) {
  const ctx = getCtx('chart-trend');
  if (!ctx) return;

  const gradient = ctx.createLinearGradient(0, 0, 0, 220);
  gradient.addColorStop(0,   'rgba(0,212,255,0.25)');
  gradient.addColorStop(1,   'rgba(0,212,255,0.00)');

  const config = {
    type: 'line',
    data: {
      labels:   d.trendLabels,
      datasets: [{
        data:            d.trendConvs,
        borderColor:     C.accent,
        backgroundColor: gradient,
        borderWidth:     2,
        pointRadius:     3,
        pointHoverRadius:5,
        pointBackgroundColor: C.accent,
        tension:         0.4,
        fill:            true,
      }]
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode: 'index', intersect: false },
      scales: {
        x: { grid: { color: C.grid }, ticks: { maxTicksLimit: 7 } },
        y: { grid: { color: C.grid }, beginAtZero: true, ticks: { stepSize: 1 } },
      },
    }
  };

  if (charts['trend']) {
    charts['trend'].data.labels   = d.trendLabels;
    charts['trend'].data.datasets[0].data = d.trendConvs;
    charts['trend'].update('none');
  } else {
    charts['trend'] = new Chart(ctx, config);
  }
}

// ── HOURLY BAR ────────────────────────────────────────────────────────────────
function renderHourlyChart(d) {
  const ctx = getCtx('chart-hourly');
  if (!ctx) return;

  const labels = Array.from({ length: 24 }, (_, i) =>
    i === 0 ? '12am' : i < 12 ? i + 'am' : i === 12 ? '12pm' : (i - 12) + 'pm'
  );

  const config = {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data:            d.hourly,
        backgroundColor: d.hourly.map(v => v === Math.max(...d.hourly) ? C.accent : 'rgba(0,212,255,0.2)'),
        borderRadius:    3,
        borderSkipped:   false,
      }]
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { grid: { color: C.grid }, beginAtZero: true },
      },
    }
  };

  if (charts['hourly']) {
    charts['hourly'].data.datasets[0].data            = d.hourly;
    charts['hourly'].data.datasets[0].backgroundColor = d.hourly.map(v => v === Math.max(...d.hourly) ? C.accent : 'rgba(0,212,255,0.2)');
    charts['hourly'].update('none');
  } else {
    charts['hourly'] = new Chart(ctx, config);
  }
}

// ── HEATMAP ───────────────────────────────────────────────────────────────────
function renderHeatmap(d) {
  const wrap = document.getElementById('heatmap-grid');
  if (!wrap) return;

  const days  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const grid  = d.heatmapGrid; // 7×24

  const maxVal = Math.max(...grid.flat(), 1);

  let html = '';
  grid.forEach((row, di) => {
    html += `<div class="heatmap-row">
      <span class="heatmap-day-label">${days[di]}</span>`;
    row.forEach((val, hi) => {
      const intensity = Math.round((val / maxVal) * 5);
      const tip = `${days[di]} ${hi}:00 — ${val} jobs`;
      html += `<div class="heatmap-cell heat-${intensity}" data-tip="${tip}"></div>`;
    });
    html += '</div>';
  });

  // Hour labels row
  html += '<div class="heatmap-hours">';
  html += '<div></div>';
  for (let h = 0; h < 24; h++) {
    html += `<div class="heatmap-hour-label">${h % 6 === 0 ? h : ''}</div>`;
  }
  html += '</div>';

  wrap.innerHTML = html;
}

// ── FORMAT SPARK BARS ─────────────────────────────────────────────────────────
function renderFormatBars(d) {
  renderSparkList('spark-input',  d.inputFormats);
  renderSparkList('spark-output', d.outputFormats);
  renderStrategyBars(d);
}

function renderSparkList(id, items) {
  const el = document.getElementById(id);
  if (!el || !items.length) return;

  const max = Math.max(...items.map(i => i.val), 1);
  el.innerHTML = items.map(item => `
    <div class="spark-item">
      <span class="spark-label">${item.name}</span>
      <div class="spark-track">
        <div class="spark-fill" style="width:${Math.round(item.val / max * 100)}%"></div>
      </div>
      <span class="spark-val">${item.val}</span>
    </div>
  `).join('');
}

function renderStrategyBars(d) {
  const el = document.getElementById('spark-strategy');
  if (!el || !d.strategies.length) return;

  const colors = [C.green, C.accent, C.amber, C.purple];
  const max    = Math.max(...d.strategies.map(i => i.val), 1);
  el.innerHTML = d.strategies.map((item, i) => `
    <div class="spark-item">
      <span class="spark-label">${item.name}</span>
      <div class="spark-track">
        <div class="spark-fill" style="width:${Math.round(item.val / max * 100)}%;background:${colors[i % colors.length]}"></div>
      </div>
      <span class="spark-val">${item.val}</span>
    </div>
  `).join('');
}

// ── STRATEGY DONUT ────────────────────────────────────────────────────────────
function renderStrategyRing(d) {
  const ctx = getCtx('chart-strategy');
  if (!ctx || !d.strategies.length) return;

  const labels = d.strategies.map(s => s.name);
  const values = d.strategies.map(s => s.val);
  const colors = [C.green, C.accent, C.amber, C.purple];

  // Legend
  const legEl = document.getElementById('strategy-legend');
  if (legEl) {
    legEl.innerHTML = d.strategies.map((s, i) => `
      <div class="ring-legend-item">
        <div class="ring-legend-dot" style="background:${colors[i % colors.length]}"></div>
        <span class="ring-legend-label">${s.name}</span>
        <span class="ring-legend-val">${s.val}</span>
      </div>
    `).join('');
  }

  const config = {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data:            values,
        backgroundColor: colors,
        borderWidth:     0,
        hoverOffset:     4,
      }]
    },
    options: {
      responsive:          true,
      maintainAspectRatio: true,
      cutout:              '72%',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${ctx.parsed}` } },
      }
    }
  };

  if (charts['strategy']) {
    charts['strategy'].data.datasets[0].data = values;
    charts['strategy'].update('none');
  } else {
    charts['strategy'] = new Chart(ctx, config);
  }
}

// ── RECENT JOBS TABLE ─────────────────────────────────────────────────────────
function renderRecentJobs(d) {
  const tbody = document.getElementById('jobs-tbody');
  if (!tbody) return;

  if (!d.recentJobs.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No jobs yet</td></tr>';
    return;
  }

  tbody.innerHTML = d.recentJobs.map(j => `
    <tr>
      <td>
        <div style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${j.name}">${j.name}</div>
      </td>
      <td class="td-muted">${j.inFmt.toUpperCase()}</td>
      <td><span style="color:var(--accent)">${j.outFmt.toUpperCase()}</span></td>
      <td class="td-muted">${j.size}</td>
      <td>
        <div class="progress-bar-wrap">
          <div class="progress-bar-fill" style="width:${j.progress}%"></div>
        </div>
      </td>
      <td>${statusChip(j.status)}</td>
      <td class="td-muted">${j.when}</td>
    </tr>
  `).join('');
}

function statusChip(status) {
  const map = {
    done:       '<span class="chip chip-done">✓ done</span>',
    error:      '<span class="chip chip-error">✗ error</span>',
    cancelled:  '<span class="chip chip-cancel">✕ cancelled</span>',
    converting: '<span class="chip chip-active">⟳ active</span>',
    queued:     '<span class="chip chip-queued">⧖ queued</span>',
    paused:     '<span class="chip chip-cancel">⏸ paused</span>',
  };
  return map[status] || `<span class="chip">${status}</span>`;
}

// ── VISITORS ──────────────────────────────────────────────────────────────────
function renderVisitorKPIs(d) {
  setText('vis-total',       fmt(d.totalVisitors));
  setText('vis-new',         fmt(d.newVisitors));
  setText('vis-returning',   fmt(d.returningVisitors));
  setText('vis-retention',   d.retentionRate + '%');
  setText('vis-active30',    fmt(d.activeVisitors30d));
  // secondary panel
  setText('vis-total-b',     fmt(d.totalVisitors));
  setText('vis-returning-b', fmt(d.returningVisitors));
  setText('vis-retention-b', d.retentionRate + '%');
  setText('vis-active30-b',  fmt(d.activeVisitors30d));
}

// ── USERS ─────────────────────────────────────────────────────────────────────
function renderUserKPIs(d) {
  setText('u-total',   fmt(d.totalUsers));
  setText('u-7d',      '+' + d.newUsers7d);
  setText('u-30d',     '+' + d.newUsers30d);
  setText('u-paid',    fmt(d.paidUsers));
}

function renderUserTable(d) {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  if (!d.recentUsers.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No users yet</td></tr>';
    return;
  }

  tbody.innerHTML = d.recentUsers.map(u => `
    <tr>
      <td>
        ${u.username}
        ${u.isPaid ? '<span class="paid-tag">paid</span>' : ''}
      </td>
      <td class="td-muted">${u.email || '—'}</td>
      <td class="td-muted">${u.joined}</td>
      <td style="color:var(--amber)">${u.credits}</td>
      <td class="td-muted">${u.freeUsed}</td>
      <td style="color:var(--accent)">${u.jobs}</td>
    </tr>
  `).join('');
}

// ── FEEDBACK ──────────────────────────────────────────────────────────────────
function renderFeedback(d) {
  const el = document.getElementById('feedback-list');
  if (!el) return;

  if (!d.feedback.length) {
    el.innerHTML = '<div class="empty-state">No feedback yet</div>';
    return;
  }

  el.innerHTML = d.feedback.map(fb => `
    <div class="feedback-card">
      <div class="feedback-meta">
        <span class="feedback-category">${fb.category}</span>
        <span class="feedback-who">${fb.username ? '@' + fb.username : 'anonymous'}</span>
        <span class="feedback-when">${fb.when}</span>
      </div>
      <div class="feedback-msg">${escHtml(fb.message)}</div>
    </div>
  `).join('');
}

// ── ONBOARDING ────────────────────────────────────────────────────────────────
function renderOnboarding(d) {
  // KPIs
  setText('ob-completed', fmt(d.obCompleted));
  setText('ob-skipped',   fmt(d.obSkipped));
  setText('ob-inprog',    fmt(d.obInProgress));
  setText('ob-never',     fmt(d.obNever));
  // funnel count labels
  setText('ob-completed-b', fmt(d.obCompleted));
  setText('ob-skipped-b',   fmt(d.obSkipped));
  setText('ob-inprog-b',    fmt(d.obInProgress));
  setText('ob-never-b',     fmt(d.obNever));

  // Funnel bars
  const total = (d.obCompleted + d.obSkipped + d.obInProgress + d.obNever) || 1;
  renderFunnelBar('fbar-completed', d.obCompleted, total, '#00e676');
  renderFunnelBar('fbar-skipped',   d.obSkipped,   total, '#ffb700');
  renderFunnelBar('fbar-inprog',    d.obInProgress,total, '#00d4ff');
  renderFunnelBar('fbar-never',     d.obNever,     total, '#5e7a96');

  // Onboarding records table
  const tbody = document.getElementById('ob-tbody');
  if (!tbody) return;
  if (!d.obRecords.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No records yet</td></tr>';
    return;
  }
  tbody.innerHTML = d.obRecords.map(r => `
    <tr>
      <td>${r.username}</td>
      <td><span class="ob-step-pill">${r.step}</span></td>
      <td>${r.completed
        ? '<span class="chip chip-done">✓ done</span>'
        : '<span class="chip chip-active">in progress</span>'}</td>
      <td class="td-muted">${r.created_at}</td>
      <td class="td-muted">${r.completed_at || '—'}</td>
    </tr>
  `).join('');
}

function renderFunnelBar(id, val, total, color) {
  const el = document.getElementById(id);
  if (!el) return;
  const pct = Math.round(val / total * 100);
  el.style.width      = pct + '%';
  el.style.background = color;
  el.textContent      = pct + '%';
}

// ── SERVER CLOCK ──────────────────────────────────────────────────────────────
function setupServerClock() {
  const el = document.getElementById('server-time');
  if (!el) return;
  function tick() {
    el.textContent = new Date().toLocaleTimeString('en-US', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  }
  tick();
  setInterval(tick, 1000);
}

// ── MANUAL REFRESH ────────────────────────────────────────────────────────────
function manualRefresh() {
  const btn = document.getElementById('btn-refresh');
  if (btn) btn.classList.add('refreshing');
  fetchAndRender().finally(() => {
    if (btn) btn.classList.remove('refreshing');
  });
}

// ── LOADER ────────────────────────────────────────────────────────────────────
function hideLoader() {
  const el = document.getElementById('loading-overlay');
  if (el) el.classList.add('hidden');
  setTimeout(() => { if (el) el.remove(); }, 500);
}

// ── UTILS ─────────────────────────────────────────────────────────────────────
function getCtx(id) {
  const canvas = document.getElementById(id);
  return canvas ? canvas.getContext('2d') : null;
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmt(n) {
  if (n === undefined || n === null) return '—';
  return Number(n).toLocaleString();
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Expose for inline onclick
window.manualRefresh = manualRefresh;