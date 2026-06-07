/* ── ExportReady Analytics Dashboard ── */

const API_URL    = '/analytics/api/';
const REFRESH_MS = 30_000;

let trendChart   = null;
let hourlyChart  = null;
let allUsers     = [];
let allFeedback  = [];
let refreshTimer = null;

/* ═══════════════════════════════
   INIT
═══════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  initNav();
  initSearch();
  initFeedbackSearch();
  initHamburger();
  fetchData();
  refreshTimer = setInterval(fetchData, REFRESH_MS);
});

/* ═══════════════════════════════
   HAMBURGER
═══════════════════════════════ */
function initHamburger() {
  const btn     = document.getElementById('hamburgerBtn');
  const sidebar = document.querySelector('.sidebar');
  if (!btn || !sidebar) return;

  const overlay = document.createElement('div');
  overlay.className = 'sidebar-overlay';
  document.body.appendChild(overlay);

  function openMenu() {
    sidebar.classList.add('open');
    overlay.classList.add('open');
    btn.classList.add('open');
  }
  function closeMenu() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    btn.classList.remove('open');
  }

  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    sidebar.classList.contains('open') ? closeMenu() : openMenu();
  });

  overlay.addEventListener('click', closeMenu);

  document.querySelectorAll('.nav-item[data-section]').forEach(function(item) {
    item.addEventListener('click', function() {
      if (window.innerWidth <= 900) closeMenu();
    });
  });
}

/* ═══════════════════════════════
   NAV
═══════════════════════════════ */
function initNav() {
  document.querySelectorAll('.nav-item[data-section]').forEach(function(item) {
    item.addEventListener('click', function(e) {
      e.preventDefault();
      var target = item.dataset.section;
      switchSection(target);
      document.querySelectorAll('.nav-item').forEach(function(n) {
        n.classList.remove('active');
      });
      item.classList.add('active');
    });
  });
}

function switchSection(name) {
  document.querySelectorAll('.section').forEach(function(s) {
    s.classList.remove('active');
  });
  var sec = document.getElementById('section-' + name);
  if (sec) sec.classList.add('active');
  var titles = { overview: 'Overview', jobs: 'Jobs', users: 'Users', feedback: 'Feedback' };
  document.getElementById('pageTitle').textContent = titles[name] || name;
}

/* ═══════════════════════════════
   FETCH
═══════════════════════════════ */
async function fetchData() {
  try {
    var res  = await fetch(API_URL);
    if (!res.ok) throw new Error(res.status);
    var data = await res.json();
    render(data);
    setStatus(true);
  } catch (err) {
    setStatus(false);
    console.error('Analytics fetch failed:', err);
  }
}

/* ═══════════════════════════════
   STATUS
═══════════════════════════════ */
function setStatus(online) {
  var dot  = document.getElementById('statusDot');
  var text = document.getElementById('statusText');
  dot.className    = 'status-dot ' + (online ? 'online' : 'offline');
  text.textContent = online ? 'Server online' : 'Server offline';
  var sync = document.getElementById('lastSync');
  sync.textContent = online
    ? 'Last sync: ' + new Date().toLocaleTimeString()
    : 'Sync failed';
}

/* ═══════════════════════════════
   RENDER
═══════════════════════════════ */
function render(d) {
  renderKPIs(d);
  renderTrend(d);
  renderHourly(d);
  renderBars('outputFormats', d.outputFormats);
  renderBars('inputFormats',  d.inputFormats);
  renderBars('strategies',    d.strategies);
  renderRetention(d);
  renderJobs(d.recentJobs);
  renderUsers(d.recentUsers);
  allUsers    = d.recentUsers || [];
  allFeedback = d.feedback    || [];
  renderFeedback(allFeedback);
  
}

/* ── KPIs ── */
function renderKPIs(d) {
  setText('kpiTotal',      fmt(d.totalJobs));
  setText('kpiActive',     fmt(d.activeJobs) + ' active');
  setText('kpiDone',       fmt(d.totalDone));
  setText('kpiRate',       d.successRate + '% success');
  setText('kpiErrors',     fmt(d.totalErrors));
  setText('kpiCancelled',  fmt(d.totalCancelled) + ' cancelled');
  setText('kpiData',       d.dataHuman || '0 B');
  setText('kpiUsers',      fmt(d.totalUsers));
  setText('kpiNewUsers',   '+' + fmt(d.newUsers7d) + ' this week');
  setText('kpiPaid',       fmt(d.paidUsers));
  setText('kpiNewUsers30', '+' + fmt(d.newUsers30d) + ' this month');
  setText('uTotal', fmt(d.totalUsers));
  setText('uNew7',  fmt(d.newUsers7d));
  setText('uNew30', fmt(d.newUsers30d));
  setText('uPaid',  fmt(d.paidUsers));
}

/* ── TREND CHART ── */
function renderTrend(d) {
  var ctx = document.getElementById('trendChart').getContext('2d');
  if (trendChart) { trendChart.destroy(); }
  trendChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: d.trendLabels,
      datasets: [{
        label: 'Conversions',
        data: d.trendConvs,
        backgroundColor: 'rgba(102,0,255,0.15)',
        borderColor: '#6600ff',
        borderWidth: 2,
        borderRadius: 4,
        hoverBackgroundColor: 'rgba(102,0,255,0.3)'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function(ctx) { return ' ' + ctx.parsed.y + ' conversions'; } } }
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 11 }, color: '#6b7280' } },
        y: { beginAtZero: true, grid: { color: '#f3f4f6' }, ticks: { stepSize: 1, font: { size: 11 }, color: '#6b7280' } }
      }
    }
  });
}

/* ── HOURLY CHART ── */
function renderHourly(d) {
  var ctx = document.getElementById('hourlyChart').getContext('2d');
  if (hourlyChart) { hourlyChart.destroy(); }
  var labels = Array.from({length: 24}, function(_, i) {
    if (i === 0)  return '12a';
    if (i === 12) return '12p';
    return i < 12 ? i + 'a' : (i - 12) + 'p';
  });
  hourlyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Jobs',
        data: d.hourly,
        fill: true,
        backgroundColor: 'rgba(59,130,246,0.08)',
        borderColor: '#3b82f6',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: '#3b82f6',
        tension: 0.4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 10 }, color: '#6b7280', maxTicksLimit: 8 } },
        y: { beginAtZero: true, grid: { color: '#f3f4f6' }, ticks: { stepSize: 1, font: { size: 10 }, color: '#6b7280' } }
      }
    }
  });
}

/* ── BAR LISTS ── */
function renderBars(containerId, items) {
  var el = document.getElementById(containerId);
  if (!el || !items || !items.length) {
    if (el) el.innerHTML = '<div style="padding:14px 18px;color:#9ca3af;font-size:12px">No data yet</div>';
    return;
  }
  var max = Math.max.apply(null, items.map(function(i) { return i.val; }).concat([1]));
  el.innerHTML = items.map(function(item) {
    return '<div class="bar-item">'
      + '<div class="bar-item-top">'
      + '<span class="bar-item-name">' + esc(item.name) + '</span>'
      + '<span class="bar-item-val">'  + item.val       + '</span>'
      + '</div>'
      + '<div class="bar-track"><div class="bar-fill" style="width:' + Math.round((item.val / max) * 100) + '%"></div></div>'
      + '</div>';
  }).join('');
}

/* ── RETENTION ── */
function renderRetention(d) {
  var el = document.getElementById('retentionStats');
  if (!el) return;
  el.innerHTML =
    '<div class="retention-row"><span class="retention-label">Total Visitors</span><span class="retention-val">'          + fmt(d.totalVisitors)    + '</span></div>' +
    '<div class="retention-row"><span class="retention-label">Returning</span><span class="retention-badge badge-green">'  + fmt(d.returningVisitors) + '</span></div>' +
    '<div class="retention-row"><span class="retention-label">New</span><span class="retention-badge badge-blue">'         + fmt(d.newVisitors)       + '</span></div>' +
    '<div class="retention-row"><span class="retention-label">Retention Rate</span><span class="retention-badge badge-purple">' + d.retentionRate + '%</span></div>' +
    '<div class="retention-row"><span class="retention-label">Active (30d)</span><span class="retention-val">'             + fmt(d.activeVisitors30d) + '</span></div>';
}

/* ── JOBS TABLE ── */
function renderJobs(jobs) {
  var tbody = document.getElementById('jobsBody');
  var count = document.getElementById('jobCount');
  if (!tbody) return;
  if (!jobs || !jobs.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#9ca3af;padding:32px">No jobs yet</td></tr>';
    return;
  }
  if (count) count.textContent = jobs.length + ' recent';
  tbody.innerHTML = jobs.map(function(j) {
    return '<tr>'
      + '<td><div class="cell-filename" title="' + esc(j.name) + '">' + esc(j.name) + '</div></td>'
      + '<td><span class="fmt-badge">' + esc(j.inFmt) + '</span><span style="margin:0 4px;color:#d1d5db">&rarr;</span><span class="fmt-badge">' + esc(j.outFmt) + '</span></td>'
      + '<td style="white-space:nowrap">' + esc(j.size) + '</td>'
      + '<td style="font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(j.strategy) + '">' + (esc(j.strategy) || '&mdash;') + '</td>'
      + '<td><span class="status-badge status-' + j.status + '">' + statusDot(j.status) + ' ' + esc(j.status) + '</span></td>'
      + '<td style="white-space:nowrap;color:#9ca3af;font-size:12px">' + esc(j.when) + '</td>'
      + '</tr>';
  }).join('');
}

/* ── USERS TABLE ── */
function renderUsers(users) {
  var tbody = document.getElementById('usersBody');
  if (!tbody) return;
  if (!users || !users.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#9ca3af;padding:32px">No users yet</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(function(u) {
    return '<tr>'
      + '<td style="font-weight:600;color:#111827">' + esc(u.username) + '</td>'
      + '<td style="color:#6b7280;font-size:12px">'  + (esc(u.email) || '&mdash;') + '</td>'
      + '<td><span class="plan-badge ' + (u.isPaid ? 'plan-paid' : 'plan-free') + '">' + (u.isPaid ? 'Paid' : 'Free') + '</span></td>'
      + '<td style="font-weight:600">' + fmt(u.credits)  + '</td>'
      + '<td>'                         + fmt(u.freeUsed) + '</td>'
      + '<td style="font-weight:600">' + fmt(u.jobs)     + '</td>'
      + '<td style="color:#9ca3af;font-size:12px;white-space:nowrap">' + esc(u.joined) + '</td>'
      + '</tr>';
  }).join('');
}

/* ═══════════════════════════════
   SEARCH
═══════════════════════════════ */
function initSearch() {
  var input = document.getElementById('userSearch');
  if (!input) return;
  input.addEventListener('input', function() {
    var q = input.value.toLowerCase().trim();
    if (!q) { renderUsers(allUsers); return; }
    renderUsers(allUsers.filter(function(u) {
      return u.username.toLowerCase().includes(q) || (u.email || '').toLowerCase().includes(q);
    }));
  });
}

/* ═══════════════════════════════
   FEEDBACK
═══════════════════════════════ */
function renderFeedback(items) {
  var tbody = document.getElementById('feedbackBody');
  if (!tbody) return;

  setText('fbTotal',   fmt(items.length));
  setText('fbBugs',    fmt(items.filter(function(f) { return f.category === 'bug'; }).length));
  setText('fbSpeed',   fmt(items.filter(function(f) { return f.category === 'speed'; }).length));
  setText('fbQuality', fmt(items.filter(function(f) { return f.category === 'export-quality'; }).length));
  setText('fbOther',   fmt(items.filter(function(f) { return !['bug','speed','export-quality'].includes(f.category); }).length));

  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#9ca3af;padding:32px">No feedback yet</td></tr>';
    return;
  }

  var categoryLabels = {
    'export-quality': 'Export quality',
    'speed':          'Speed',
    'ai-chat':        'Export AI',
    'ui':             'UI',
    'bug':            'Bug',
    'other':          'Other'
  };

  tbody.innerHTML = items.map(function(f) {
    var userCell = f.username
      ? '<span>' + esc(f.username) + '</span>'
      : '<span style="color:#9ca3af;font-style:italic">Anonymous</span>';
    return '<tr>'
      + '<td style="font-weight:600;color:#111827;white-space:nowrap">' + userCell + '</td>'
      + '<td><span class="cat-badge cat-' + esc(f.category) + '">' + esc(categoryLabels[f.category] || f.category) + '</span></td>'
      + '<td><div class="feedback-msg" title="' + esc(f.message) + '">' + esc(f.message) + '</div></td>'
      + '<td style="font-size:11px;color:#9ca3af;white-space:nowrap">' + (esc(f.ip) || '&mdash;') + '</td>'
      + '<td style="font-size:12px;color:#9ca3af;white-space:nowrap">' + esc(f.when) + '</td>'
      + '</tr>';
  }).join('');
}

function initFeedbackSearch() {
  var input = document.getElementById('feedbackSearch');
  if (!input) return;
  input.addEventListener('input', function() {
    var q = input.value.toLowerCase().trim();
    if (!q) { renderFeedback(allFeedback); return; }
    renderFeedback(allFeedback.filter(function(f) {
      return (f.message  || '').toLowerCase().includes(q)
          || (f.username || '').toLowerCase().includes(q)
          || (f.category || '').toLowerCase().includes(q);
    }));
  });
}

/* ═══════════════════════════════
   HELPERS
═══════════════════════════════ */
function setText(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmt(n) {
  if (n === undefined || n === null) return '\u2014';
  return Number(n).toLocaleString();
}

function esc(str) {
  if (!str && str !== 0) return '';
  return String(str)
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;');
}

function statusDot(status) {
  var dots = { done: '\u25CF', error: '\u25CF', cancelled: '\u25CB', converting: '\u25CF', queued: '\u25CB', paused: '\u25D0' };
  return dots[status] || '\u25CB';
}
