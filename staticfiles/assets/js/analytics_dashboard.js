const PAL = ['#4f8ef7','#34d399','#fbbf24','#a78bfa','#f87171','#2dd4bf'];
let range = '7d';

// ── DRAWER ────────────────────────────────────────────────────────────────────
function openDrawer() {
  const overlay = document.getElementById('drawerOverlay');
  const drawer  = document.getElementById('drawer');
  overlay.style.display = 'block';
  requestAnimationFrame(() => {
    overlay.classList.add('open');
    drawer.classList.add('open');
  });
  document.body.style.overflow = 'hidden';
}
function closeDrawer() {
  const overlay = document.getElementById('drawerOverlay');
  const drawer  = document.getElementById('drawer');
  overlay.classList.remove('open');
  drawer.classList.remove('open');
  setTimeout(() => { overlay.style.display = 'none'; }, 250);
  document.body.style.overflow = '';
}

// ── FETCH REAL DATA ───────────────────────────────────────────────────────────
async function loadData() {
  const res = await fetch('/analytics/api/');
  if (res.status === 401) { window.location.href = '/analytics/login/'; return null; }
  if (!res.ok) throw new Error('API error');
  return await res.json();
}

// ─────────────────────────────────────────────────────────────────────────────
async function render() {
  let d;
  try { d = await loadData(); } catch { return; }
  if (!d) return;

  const now = new Date().toLocaleString('en', { weekday: 'long', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  document.getElementById('nowLabel').textContent = now;
  document.getElementById('syncTime').textContent = 'just now';
  const drawerSync = document.getElementById('syncTimeDrawer');
  if (drawerSync) drawerSync.textContent = 'just now';

  // ── KPIs ──
  setVal('kv1', d.totalJobs.toLocaleString());
  setVal('kv2', d.totalDone.toLocaleString());
  setVal('kv3', d.activeJobs > 0 ? d.activeJobs + ' active' : '—');
  setVal('kv4', d.dataHuman);
  document.getElementById('kd1').innerHTML = `${d.totalErrors} errors · ${d.totalCancelled} cancelled`;
  document.getElementById('kd2').innerHTML = `<span style="color:var(--green)">${d.successRate}%</span> success rate`;
  document.getElementById('kd4').innerHTML = `from ${d.totalDone} completed conversion${d.totalDone !== 1 ? 's' : ''}`;

  // ── CHARTS ──
  const labels = d.trendLabels;
  const convs  = d.trendConvs;
  const vis    = convs.map(v => v);
  drawLine(labels, vis, convs);
  drawBar(d.hourly);

  const outTotal = d.outputFormats.reduce((a, b) => a + b.val, 0) || 1;
  const outFmts  = d.outputFormats.map(f => ({ ...f, pct: Math.round(f.val / outTotal * 100) }));
  drawDonut(outFmts);

  const days7 = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const heatmap = days7.map((day, i) => ({ day, hours: d.heatmapGrid[i] }));
  drawHeatmap(heatmap);

  const errPct = d.totalJobs > 0 ? Math.round(d.totalErrors / d.totalJobs * 100) : 0;
  const cxlPct = d.totalJobs > 0 ? Math.round(d.totalCancelled / d.totalJobs * 100) : 0;
  drawGauge(d.successRate, errPct, cxlPct);

  const stMax = Math.max(...(d.strategies.map(s => s.val)), 1);
  drawStats('strategyStats', d.strategies.map(s => ({ ...s, pct: Math.round(s.val / stMax * 100) })), '#4f8ef7');

  const inMax = Math.max(...(d.inputFormats.map(f => f.val)), 1);
  drawStats('inputFmtStats', d.inputFormats.map(f => ({ ...f, pct: Math.round(f.val / inMax * 100) })), '#34d399');

  drawJobs(d.recentJobs);

  // ── Retention KPIs ──
  setVal('rv1', d.totalVisitors.toLocaleString());
  setVal('rv2', d.returningVisitors.toLocaleString());
  setVal('rv3', d.newVisitors.toLocaleString());
  setVal('rv4', d.activeVisitors30d.toLocaleString());
  document.getElementById('rd1').innerHTML = `${d.activeVisitors30d} active in last 30 days`;
  document.getElementById('rd2').innerHTML = `<span style="color:var(--green)">${d.retentionRate}%</span> retention rate`;
  document.getElementById('rd3').innerHTML = `${d.returningVisitors} came back`;
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.opacity = '0';
  requestAnimationFrame(() => {
    el.textContent = val;
    el.style.transition = 'opacity .2s';
    el.style.opacity = '1';
  });
}

// ── LINE CHART ────────────────────────────────────────────────────────────────
function drawLine(labels, vis, convs) {
  const el = document.getElementById('lineChart');
  const W = 480, H = 130, pl = 36, pr = 8, pt = 8, pb = 22;
  const iW = W - pl - pr, iH = H - pt - pb;
  const n = labels.length;
  const maxV = Math.max(...vis, ...convs, 1) * 1.18;
  const xp = i => pl + (i / Math.max(n - 1, 1)) * iW;
  const yp = v => pt + iH - (v / maxV) * iH;
  let s = '';
  [0, .25, .5, .75, 1].forEach(t => {
    const y = pt + iH * (1 - t);
    s += `<line class="gl" x1="${pl}" y1="${y}" x2="${W - pr}" y2="${y}"/>`;
    if (t > 0) s += `<text class="ax" x="${pl - 4}" y="${y + 3}" text-anchor="end">${Math.round(maxV * t)}</text>`;
  });
  const step = n > 10 ? 3 : n > 5 ? 2 : 1;
  labels.forEach((l, i) => {
    if (i % step === 0)
      s += `<text class="ax" x="${xp(i)}" y="${H - 4}" text-anchor="middle">${l}</text>`;
  });
  const vPath = convs.map((_, i) => `${i === 0 ? 'M' : 'L'}${xp(i)},${yp(convs[i])}`).join(' ');
  const base  = `L${xp(n - 1)},${H - pb} L${xp(0)},${H - pb} Z`;
  s += `<path d="${vPath} ${base}" fill="#4f8ef7" opacity=".08"/>`;
  s += `<path d="${vPath}" fill="none" stroke="#4f8ef7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`;
  convs.forEach((v, i) => s += `<circle cx="${xp(i)}" cy="${yp(v)}" r="2.5" fill="#4f8ef7" stroke="#111" stroke-width="1.5"/>`);
  el.innerHTML = s;
}

// ── BAR CHART ─────────────────────────────────────────────────────────────────
function drawBar(hourly) {
  const el = document.getElementById('barChart');
  const W = 480, H = 100, pl = 28, pr = 6, pt = 4, pb = 18;
  const iW = W - pl - pr, iH = H - pt - pb;
  const maxV = Math.max(...hourly, 1);
  const bw = iW / 24 - 2;
  const nowH = new Date().getHours();
  let s = '';
  hourly.forEach((v, i) => {
    const x  = pl + i * (iW / 24);
    const bh = Math.max((v / maxV) * iH, v > 0 ? 2 : 0);
    const y  = pt + iH - bh;
    s += `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="1" fill="${i === nowH ? '#4f8ef7' : '#1e1e1e'}"/>`;
    if (i % 4 === 0)
      s += `<text class="ax" x="${x + bw / 2}" y="${H - 3}" text-anchor="middle">${i}h</text>`;
  });
  el.innerHTML = s;
}

// ── DONUT ─────────────────────────────────────────────────────────────────────
function drawDonut(fmts) {
  const svg    = document.getElementById('donutChart');
  const legend = document.getElementById('donutLegend');
  if (!fmts.length) {
    svg.innerHTML = '';
    legend.innerHTML = '<span style="font-size:11px;color:var(--faint)">No data yet</span>';
    return;
  }
  const total = fmts.reduce((a, b) => a + b.val, 0) || 1;
  const cx = 20, cy = 20, r = 18, ir = 11;
  let angle = -Math.PI / 2, paths = '';
  fmts.forEach((f, i) => {
    const sl = (f.val / total) * 2 * Math.PI;
    const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
    const x2 = cx + r * Math.cos(angle + sl), y2 = cy + r * Math.sin(angle + sl);
    paths += `<path d="M${cx},${cy} L${x1.toFixed(2)},${y1.toFixed(2)} A${r},${r},0,${sl > Math.PI ? 1 : 0},1,${x2.toFixed(2)},${y2.toFixed(2)} Z" fill="${PAL[i % PAL.length]}" stroke="#111" stroke-width=".8" opacity=".9"/>`;
    angle += sl;
  });
  paths += `<circle cx="${cx}" cy="${cy}" r="${ir}" fill="#111"/>`;
  svg.innerHTML = paths;
  legend.innerHTML = fmts.map((f, i) => `
    <div class="leg-item">
      <div class="leg-swatch" style="background:${PAL[i % PAL.length]}"></div>
      <span class="leg-name">${f.name}</span>
      <span class="leg-pct">${f.pct}%</span>
    </div>`).join('');
}

// ── HEATMAP ───────────────────────────────────────────────────────────────────
function drawHeatmap(hm) {
  const el = document.getElementById('heatmap');
  const maxV = Math.max(...hm.flatMap(d => d.hours), 1);
  let s = '<div class="hm-grid">';
  s += '<div></div>';
  for (let h = 0; h < 24; h++)
    s += `<div style="font-size:9px;color:#666;text-align:center;font-family:var(--mono)">${h % 4 === 0 ? h + 'h' : ''}</div>`;
  hm.forEach(({ day, hours }) => {
    s += `<div class="hm-label">${day}</div>`;
    hours.forEach(v => {
      const a = v > 0 ? 0.06 + (v / maxV) * 0.82 : 0.02;
      s += `<div class="hm-cell" style="background:rgba(79,142,247,${a.toFixed(2)})" title="${v} jobs"></div>`;
    });
  });
  s += '</div>';
  el.innerHTML = s;
}

// ── GAUGE ─────────────────────────────────────────────────────────────────────
function drawGauge(pct, errP, cxlP) {
  const svg = document.getElementById('gaugeChart');
  document.getElementById('gaugeVal').textContent = pct + '%';
  const cx = 60, cy = 60, r = 48;
  const fa = Math.PI + ((pct || 0) / 100) * Math.PI;
  const ex = cx + r * Math.cos(fa - Math.PI);
  const ey = cy + r * Math.sin(fa - Math.PI);
  svg.innerHTML = `
    <path d="M${cx - r},${cy} A${r},${r},0,0,1,${cx + r},${cy}" fill="none" stroke="#1e1e1e" stroke-width="7" stroke-linecap="round"/>
    <path d="M${cx - r},${cy} A${r},${r},0,0,1,${ex.toFixed(2)},${ey.toFixed(2)}" fill="none" stroke="#ededed" stroke-width="7" stroke-linecap="round"/>
    <text x="${cx - 44}" y="${cy - 10}" font-family="Geist Mono,monospace" font-size="9" fill="#888">Err ${errP}%</text>
    <text x="${cx + 44}" y="${cy - 10}" font-family="Geist Mono,monospace" font-size="9" fill="#888" text-anchor="end">Cxl ${cxlP}%</text>
  `;
}

// ── STAT BARS ─────────────────────────────────────────────────────────────────
function drawStats(id, items, color) {
  const el = document.getElementById(id);
  if (!items || !items.length) {
    el.innerHTML = '<p style="font-size:11px;color:var(--faint)">No data yet</p>';
    return;
  }
  el.innerHTML = items.map(it => `
    <div class="stat-item">
      <span class="stat-name">${it.name}</span>
      <div class="stat-track">
        <div class="stat-fill" style="width:${it.pct}%;background:${color}"></div>
      </div>
      <span class="stat-num">${it.val}</span>
    </div>`).join('');
}

// ── JOBS TABLE ────────────────────────────────────────────────────────────────
function drawJobs(jobs) {
  const sc = s => ({ done: 'pill-done', error: 'pill-err', cancelled: 'pill-cxl', converting: 'pill-act', queued: 'pill-act', paused: 'pill-act' }[s] || 'pill-cxl');
  if (!jobs || !jobs.length) {
    document.getElementById('jobsBody').innerHTML =
      `<tr><td colspan="6" style="text-align:center;color:var(--faint);padding:24px">No conversions yet</td></tr>`;
    return;
  }
  document.getElementById('jobsBody').innerHTML = jobs.map(j => `
    <tr>
      <td class="name" title="${j.name}">${j.name}</td>
      <td>
        <span class="fmt">${j.inFmt.toUpperCase()}</span>
        <span style="color:var(--faint);font-size:10px;margin:0 3px">→</span>
        <span class="fmt">${j.outFmt.toUpperCase()}</span>
      </td>
      <td>${j.size}</td>
      <td style="font-size:11px">${j.strategy}</td>
      <td><span class="pill ${sc(j.status)}">${j.status}</span></td>
      <td style="color:var(--faint)">${j.when}</td>
    </tr>`).join('');
}

// ── CONTROLS ──────────────────────────────────────────────────────────────────
function seg(btn, r) {
  document.querySelectorAll('.seg').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  range = r;
  render();
}

function doRefresh(btn) {
  const orig = btn.textContent;
  btn.textContent = '↻';
  render().then(() => btn.textContent = orig || '↻ Refresh');
}

// ── INIT ──────────────────────────────────────────────────────────────────────
render();
setInterval(render, 30000);
setInterval(() => {
  const now = new Date().toLocaleString('en', { weekday: 'long', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  document.getElementById('nowLabel').textContent = now;
}, 10000);