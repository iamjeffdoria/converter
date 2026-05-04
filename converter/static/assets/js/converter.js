const dropZone         = document.getElementById('dropZone');
const fileInput        = document.getElementById('fileInput');
const panel            = document.getElementById('panel');
const panelName        = document.getElementById('panelFilename');
const panelSize        = document.getElementById('panelSize');
const convTag          = document.getElementById('conversionTag');
const statusLabel      = document.getElementById('statusLabel');
const statusPct        = document.getElementById('statusPct');
const progressFill     = document.getElementById('progressFill');
const actions          = document.getElementById('actions');
const errorBox         = document.getElementById('errorBox');
const strategyTag      = document.getElementById('strategyTag');
const statSpeed        = document.getElementById('statSpeed');
const statEta          = document.getElementById('statEta');
const statElapsed      = document.getElementById('statElapsed');
const outputFormat     = document.getElementById('outputFormat');
const reconnectBanner  = document.getElementById('reconnectBanner');
const reconnectFilename= document.getElementById('reconnectFilename');


let currentJobId = null;
let pollTimer    = null;
let elapsedTimer = null;
let startTime    = null;
let isPaused     = false;
let activePlatformLabel = null;
let originalFileSize = 0;
let userModifiedSettings = false;
let chatHistory = [];

const SUPPORTED   = new Set(['.mkv','.mp4','.avi','.mov','.webm','.flv','.wmv','.ts','.m4v','.3gp']);
const SESSION_KEY = 'vc_active_job';


// ── SESSION ──
function saveSession(jobId, filename, fmt) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ jobId, filename, fmt, ts: Date.now() }));
}
function clearSession() { sessionStorage.removeItem(SESSION_KEY); }

window.addEventListener('DOMContentLoaded', async () => {
  highlightSelectedFormat();
  const raw = sessionStorage.getItem(SESSION_KEY);
  if (!raw) return;
  let saved;
  try { saved = JSON.parse(raw); } catch { clearSession(); return; }
  if (Date.now() - saved.ts > 12 * 60 * 60 * 1000) { clearSession(); return; }
  try {
    const data = await fetch(`/active-job/${saved.jobId}/`).then(r => r.json());
    if (data.error) { clearSession(); return; }
    if (['converting','queued','paused'].includes(data.status)) {
      reconnectFilename.textContent = saved.filename || data.input_name || saved.jobId;
      reconnectBanner.classList.add('active');
      reconnectBanner.addEventListener('click', () => reconnectToJob(saved, data));
    } else if (data.status === 'done') {
      reconnectToJob(saved, data);
    } else { clearSession(); }
  } catch { clearSession(); }
});

async function reconnectToJob(saved, data) {
  reconnectBanner.classList.remove('active');
  currentJobId = saved.jobId;
  panelName.textContent = saved.filename || data.input_name || '—';
  panelSize.textContent = '—';
  convTag.textContent   = saved.fmt || data.output_format?.toUpperCase() || '—';
  document.getElementById('completeBanner')?.classList.remove('active');
  errorBox.classList.remove('active');
  setStrategy(data.strategy || '');
  statElapsed.textContent = 'Reconnected ↩';
  isPaused = data.status === 'paused';
  dropZone.style.display = 'none';
  document.getElementById('settingsCard').style.display = '';
  panel.classList.add('active');
  if (data.status === 'done') {
    setStatus('done', 100); showComplete(data.filename, data.file_size); clearSession();
  } else if (data.status === 'paused') {
    setStatus('paused', data.progress, 'Paused'); showConvertingActions(); updatePauseBtn(true); pollStatus();
  } else {
    setStatus(data.status, data.progress); showConvertingActions(); pollStatus();
  }
}

function dismissReconnect(e) {
  e.stopPropagation();
  reconnectBanner.classList.remove('active');
  clearSession();
}

// ── FORMAT SELECTION ──
function setFormat(fmt) {
  outputFormat.value = fmt;
  highlightSelectedFormat();
  const extEl = document.getElementById('outputFilenameExt');
  if (extEl) extEl.textContent = '.' + fmt;
}

function highlightSelectedFormat() {
  const current = outputFormat.value;
  document.querySelectorAll('.chip').forEach(chip => {
    chip.style.borderColor = chip.textContent.toLowerCase() === current ? 'var(--accent)' : '';
    chip.style.color       = chip.textContent.toLowerCase() === current ? 'var(--accent)' : '';
  });
}
document.getElementById('settingCaptions').addEventListener('change', function () {
  document.getElementById('captionLabel').textContent = this.checked ? 'On' : 'Off';
  document.getElementById('captionStyleGroup').style.display = this.checked ? '' : 'none';
});

outputFormat.addEventListener('change', () => {
  highlightSelectedFormat();
  const extEl = document.getElementById('outputFilenameExt');
  if (extEl) extEl.textContent = '.' + outputFormat.value;
  userModifiedSettings = true;
});

document.getElementById('settingRes').addEventListener('change', () => { userModifiedSettings = true; });
document.getElementById('settingQuality').addEventListener('change', () => { userModifiedSettings = true; });
document.getElementById('settingCodec').addEventListener('change', () => { userModifiedSettings = true; });

// ── PLATFORM PRESETS ──
// Maps a platform name to the technical settings it requires.
// All settings feed into the existing setFormat() / select-based system —
// no backend changes needed.
const PLATFORM_PRESETS = {
  youtube:  { format: 'mp4', resolution: '1920x1080', quality: 'high',   codec: 'h264',  label: 'YouTube'   },
  tiktok:   { format: 'mp4', resolution: '1920x1080', quality: 'auto',   codec: 'h264',  label: 'TikTok'    },
  reels:    { format: 'mp4', resolution: '1920x1080', quality: 'auto',   codec: 'h264',  label: 'Reels'     },
  shorts:   { format: 'mp4', resolution: '1920x1080', quality: 'auto',   codec: 'h264',  label: 'Shorts'    },
  twitter:  { format: 'mp4', resolution: '1920x1080', quality: 'medium', codec: 'h264',  label: 'Twitter/X' },
  discord:  { format: 'mp4', resolution: '1280x720',  quality: 'small',  codec: 'h264',  label: 'Discord'   },
  linkedin: { format: 'mp4', resolution: '1920x1080', quality: 'high',   codec: 'h264',  label: 'LinkedIn'  },
  facebook: { format: 'mp4', resolution: '1920x1080', quality: 'auto',   codec: 'h264',  label: 'Facebook'  },
};
// ── PLATFORM SIZE LIMITS (bytes) ──
const PLATFORM_LIMITS = {
  discord:  10  * 1024 * 1024,   // 10 MB free
  twitter:  512 * 1024 * 1024,   // 512 MB
  tiktok:   287 * 1024 * 1024,   // 287 MB
  reels:    4   * 1024 * 1024 * 1024, // 4 GB
  shorts:   256 * 1024 * 1024 * 1024, // 256 GB (effectively unlimited)
  youtube:  256 * 1024 * 1024 * 1024,
  linkedin: 5   * 1024 * 1024 * 1024,
  facebook: 4   * 1024 * 1024 * 1024,
};

const PLATFORM_LIMIT_LABELS = {
  discord: '10 MB (free) / 50 MB (Nitro)',
  twitter: '512 MB',
  tiktok:  '287 MB',
  reels:   '4 GB',
};

function getPlatformSizeWarning(fileSizeBytes) {
  // Find which platform is currently active
  let activePlatform = null;
  document.querySelectorAll('.platform-chip').forEach(btn => {
    if (btn.classList.contains('active')) {
      const onclick = btn.getAttribute('onclick') || '';
      const match = onclick.match(/'(\w+)'/);
      if (match) activePlatform = match[1];
    }
  });

  if (!activePlatform) return null;

  const limit = PLATFORM_LIMITS[activePlatform];
  if (!limit || fileSizeBytes <= limit) return null;

  const label = PLATFORM_LIMIT_LABELS[activePlatform] || humanSize(limit);
  const preset = PLATFORM_PRESETS[activePlatform];
  return `Your file (${humanSize(fileSizeBytes)}) exceeds ${preset?.label || activePlatform}'s upload limit of ${label}. The export will still run but the platform may reject it. Consider using a lower quality setting.`;
}
function applyPlatformPreset(platform) {
  const preset = PLATFORM_PRESETS[platform];
  if (!preset) return;

  // Apply to all existing selects — same as AI applyAISettings does
  outputFormat.value = preset.format;
  document.getElementById('settingRes').value     = preset.resolution;
  document.getElementById('settingQuality').value = preset.quality;
  document.getElementById('settingCodec').value   = preset.codec;

  highlightSelectedFormat();
  const extEl = document.getElementById('outputFilenameExt');
  if (extEl) extEl.textContent = '.' + preset.format;
  activePlatformLabel = preset.label;
  userModifiedSettings = false;
  // Show friendly quality labels when preset is active
  const qualityFriendly = { auto: 'Balanced', high: 'High quality', medium: 'Medium', small: 'Compressed' };
  const qualitySelect = document.getElementById('settingQuality');
  Array.from(qualitySelect.options).forEach(opt => {
    opt._originalText = opt._originalText || opt.text;
    opt.text = qualityFriendly[opt.value] || opt._originalText;
  });

  // Show friendly codec labels when preset is active  
  const codecFriendly = { auto: 'Auto', h264: 'H.264 (recommended)', h265: 'H.265' };
  const codecSelect = document.getElementById('settingCodec');
  Array.from(codecSelect.options).forEach(opt => {
    opt._originalText = opt._originalText || opt.text;
    opt.text = codecFriendly[opt.value] || opt._originalText;
  });

  // Highlight the active platform chip
  document.querySelectorAll('.platform-chip').forEach(btn => {
    const isActive = btn.onclick?.toString().includes(`'${platform}'`) ||
                     btn.getAttribute('onclick')?.includes(`'${platform}'`);
    btn.classList.toggle('active', isActive);
  });

  // Also send to AI so it acknowledges the platform choice
// Also send to AI so it acknowledges the platform choice
  const chips = document.getElementById('quickChips');
  if (chips) chips.style.display = 'none';
  appendMsg('user', `Set up for ${preset.label}`);
  appendMsg('ai', `✦ Applied ${preset.label} preset — ${preset.resolution === '1920x1080' ? '1080p' : '720p'} · MP4 · ${preset.quality === 'high' ? 'High quality' : preset.quality === 'small' ? 'Compressed' : 'Balanced quality'}. Drop your file when ready.`);

  // ── RE-RUN SIZE WARNING if a file is already loaded ──
  if (pendingFile) {
    const existing = document.getElementById('sizeWarningBanner');
    if (existing) existing.remove();
    const sizeWarning = getPlatformSizeWarning(pendingFile.size);
    if (sizeWarning) {
      const banner = document.createElement('div');
      banner.id = 'sizeWarningBanner';
      banner.style.cssText = 'display:flex;align-items:flex-start;gap:10px;background:var(--warning-soft);border:1px solid var(--warning);border-left:3px solid var(--warning);padding:10px 14px;margin-bottom:12px;font-family:var(--mono);font-size:10px;color:var(--warning);line-height:1.6;';
      banner.innerHTML = `<span style="flex-shrink:0">⚠</span><span>${sizeWarning}</span>`;
      const actionsEl = document.getElementById('actions');
      if (actionsEl) actionsEl.insertBefore(banner, actionsEl.firstChild);
    }
  }
}

// ── DRAG & DROP ──
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', e => { if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('dragover'); });
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

dropZone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener('change', () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });

// ── HELPERS ──
function humanSize(bytes) {
  const units = ['B','KB','MB','GB']; let s = bytes;
  for (const u of units) { if (s < 1024) return `${s.toFixed(1)} ${u}`; s /= 1024; }
  return `${s.toFixed(1)} TB`;
}
function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
}

// ── UPLOAD ──
let pendingFile = null;
let videoObjectUrl = null;

async function handleFile(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!SUPPORTED.has(ext)) { alert(`Unsupported: ${ext}`); return; }
  if (currentJobId) { clearInterval(pollTimer); clearInterval(elapsedTimer); fetch(`/cleanup/${currentJobId}/`, { method:'POST' }); clearSession(); }

  pendingFile = file;
  originalFileSize = file.size;
  const fmt      = outputFormat.value.toUpperCase();
  const inputExt = ext.replace('.','').toUpperCase();

  dropZone.style.display = 'none';
  panel.classList.add('active');
  document.getElementById('settingsCard').style.display = '';
  document.getElementById('step2Header').style.display = '';
  document.getElementById('aiChatCol').style.display = '';
  document.getElementById('aiTeaser').style.display = 'none';
  document.getElementById('aiPanel').style.display = '';
  panelName.textContent = file.name;
  const nameWithoutExt = file.name.replace(/\.[^/.]+$/, '');
  document.getElementById('outputFilename').value = nameWithoutExt;
  document.getElementById('outputFilenameExt').textContent = '.' + outputFormat.value;
  panelSize.textContent = humanSize(file.size);
  convTag.textContent   = activePlatformLabel ? `${activePlatformLabel} Export` : `${inputExt} → ${fmt}`;
document.getElementById('completeBanner')?.classList.remove('active');
  errorBox.classList.remove('active');

  document.getElementById('thumbPreview').classList.remove('active');
  document.getElementById('sizeCompare').classList.remove('active');
  // Video preview — static element in HTML, just populate it
  if (videoObjectUrl) { URL.revokeObjectURL(videoObjectUrl); videoObjectUrl = null; }
videoObjectUrl = URL.createObjectURL(file);
const videoEl = document.getElementById('videoPreviewEl');
const previewWrap = document.getElementById('videoPreviewWrap');
const previewSizeEl = document.getElementById('previewFileSize');
if (previewSizeEl) previewSizeEl.textContent = humanSize(file.size);
if (previewWrap) previewWrap.style.display = '';

if (videoEl) {
  // Reset before assigning — avoids stale state on mobile
  videoEl.pause();
  videoEl.removeAttribute('src');
  videoEl.load();

  videoEl.src = videoObjectUrl;

  // Mobile: load() must be called after src is set, then play
  videoEl.load();
  videoEl.addEventListener('loadedmetadata', () => {
    // Muted autoplay is allowed on mobile; unmuted is blocked
    videoEl.muted = true;
    videoEl.play().catch(() => {
      // Autoplay blocked — that's fine, user can tap play manually
    });
  }, { once: true });
}

  setStrategy('');
  clearStats();
  isPaused = false;

  setStatus('queued', 0, 'AI Analyzing...');
  actions.innerHTML = `<span style="font-family:var(--mono);font-size:10px;color:var(--muted)">✦ Waiting for AI...</span>`;

  await suggestForFile(file, {});   // ← Added semicolon
  const finalFmt = outputFormat.value.toUpperCase();
  convTag.textContent = activePlatformLabel ? `${activePlatformLabel} Export` : `${inputExt} → ${finalFmt}`;

  // ── PRE-FLIGHT SIZE CHECK ──
  const sizeWarning = getPlatformSizeWarning(file.size);
  const warningHTML = sizeWarning
    ? `<div id="sizeWarningBanner" style="
        display:flex; align-items:flex-start; gap:10px;
        background:var(--warning-soft);
        border:1px solid var(--warning);
        border-left:3px solid var(--warning);
        padding:10px 14px; margin-bottom:12px;
        font-family:var(--mono); font-size:10px;
        color:var(--warning); line-height:1.6;
      ">
        <span style="flex-shrink:0">⚠</span>
        <span>${sizeWarning}</span>
      </div>`
    : '';

  actions.innerHTML = `
    ${warningHTML}
    <button class="btn btn-primary" onclick="startConvert()">▶ Export Now</button>
    <button class="btn btn-ghost" onclick="convertAnother()">✕ Cancel</button>
  `;
  setStatus('queued', 0, 'Ready');
}

async function startConvert() {
  const file = pendingFile;
  if (!file) return;

  const ext      = '.' + file.name.split('.').pop().toLowerCase();
  const fmt      = outputFormat.value.toUpperCase();
  const inputExt = ext.replace('.','').toUpperCase();

  convTag.textContent = activePlatformLabel ? `${activePlatformLabel} Export` : `${inputExt} → ${fmt}`;
  document.getElementById('outputFilenameExt').textContent = '.' + outputFormat.value;
  actions.innerHTML = '';
  setStatus('uploading', 0);

  const customName = document.getElementById('outputFilename').value.trim() || file.name.replace(/\.[^/.]+$/, '');

  const formData = new FormData();
  formData.append('file', file);
  formData.append('output_format', outputFormat.value);
  formData.append('resolution', document.getElementById('settingRes').value);
  formData.append('quality',    document.getElementById('settingQuality').value);
  formData.append('codec',      document.getElementById('settingCodec').value);
  formData.append('output_filename', customName);
  formData.append('captions', document.getElementById('settingCaptions').checked ? 'on' : 'off');
  formData.append('caption_style', document.getElementById('settingCaptionStyle').value);

const xhr = new XMLHttpRequest();
xhr.open('POST', '/upload/');

// Mobile: set explicit timeout (default is often 0=infinite but mobile OS kills it)
xhr.timeout = 300000; // 5 minutes

// Add CSRF token explicitly — some mobile browsers don't send cookies reliably
const csrfToken = getCookie('csrftoken');
if (csrfToken) xhr.setRequestHeader('X-CSRFToken', csrfToken);

xhr.upload.onprogress = e => {
  if (e.lengthComputable) {
    const pct = Math.round(e.loaded / e.total * 100);
    setStatus('uploading', pct, `Uploading… ${pct}%`);
  }
};

xhr.onload = () => {
  try {
    const data = JSON.parse(xhr.responseText);
    if (xhr.status === 401) {
      showLoginPrompt();
      panel.classList.remove('active');
      dropZone.style.display = '';
      return;
    }
    if (xhr.status !== 200 || data.error) {
      showError(data.error || 'Upload failed.');
      return;
    }
    currentJobId = data.job_id;
    startTime = Date.now();
    saveSession(currentJobId, file.name, `${inputExt} → ${fmt}`);
    elapsedTimer = setInterval(() => {
      if (!isPaused) statElapsed.textContent = `Elapsed: ${fmtElapsed(Date.now() - startTime)}`;
    }, 1000);
    setStatus('converting', 0);
    showConvertingActions();
    pollStatus();
  } catch (e) {
    showError('Server error: ' + (e.message || 'Unknown'));
  }
};

xhr.ontimeout = () => showError('Upload timed out. Check your connection and try again.');
xhr.onerror = () => {
  // Provide a more specific error for mobile debugging
  const online = navigator.onLine;
  showError(online
    ? 'Upload failed (server rejected). File may be too large or unsupported.'
    : 'No internet connection detected. Please check your network.');
};
xhr.send(formData);
}

// ── STATUS HELPERS ──
function setStrategy(text) {
  if (text) { strategyTag.textContent = text; strategyTag.style.display = ''; }
  else { strategyTag.style.display = 'none'; }
}

function clearStats() {
  statSpeed.style.display = 'none'; statSpeed.innerHTML = '';
  statEta.style.display   = 'none'; statEta.innerHTML   = '';
  statElapsed.textContent = '';
}

function setStatSpeed(val) {
  if (val) { statSpeed.innerHTML = `Speed: <strong>${val}</strong>`; statSpeed.style.display = ''; }
  else { statSpeed.style.display = 'none'; }
}
function setStatEta(val) {
  if (val) { statEta.innerHTML = `ETA: <strong>${val}</strong>`; statEta.style.display = ''; }
  else { statEta.style.display = 'none'; }
}

function setStatus(state, pct, labelOverride) {
  const labels = { uploading:'Uploading', queued:'Queued', converting:'Exporting', paused:'Paused', done:'Done', error:'Error', cancelled:'Cancelled' };
  statusLabel.className = `status-label ${state}`;
  statusLabel.textContent = labelOverride || labels[state] || state;
  statusPct.textContent = `${pct}%`;
  progressFill.style.width = pct + '%';
  progressFill.className = 'progress-fill';
  if (state === 'queued')     progressFill.classList.add('indeterminate');
  if (state === 'converting') progressFill.classList.add('converting-fill');
  if (state === 'paused')     progressFill.classList.add('paused');
  if (state === 'done')       progressFill.classList.add('done-fill');
  statusPct.style.color = state === 'done' ? 'var(--success)' : state === 'paused' ? 'var(--warning)' : '';
}

// ── POLL ──
function pollStatus() {
  pollTimer = setInterval(async () => {
    try {
      const data = await fetch(`/status/${currentJobId}/`).then(r => r.json());
      if (data.status === 'converting' || data.status === 'queued') {
        isPaused = false;
        setStatus(data.status, data.progress);
        if (data.strategy) setStrategy(data.strategy);
        setStatSpeed(data.speed); setStatEta(data.eta);
        updatePauseBtn(false);
      } else if (data.status === 'paused') {
        isPaused = true;
        setStatus('paused', data.progress, 'Paused');
        setStatSpeed(''); setStatEta(''); updatePauseBtn(true);
      } else if (data.status === 'done') {
        clearInterval(pollTimer); clearInterval(elapsedTimer);
        setStatus('done', 100);
        if (data.strategy) setStrategy(data.strategy);
        setStatSpeed(''); setStatEta('');
        statElapsed.textContent = startTime ? `Done in ${fmtElapsed(Date.now()-startTime)}` : '';
        showComplete(data.filename, data.file_size); clearSession();
      } else if (data.status === 'cancelled') {
        clearInterval(pollTimer); clearInterval(elapsedTimer);
        setStatus('cancelled', 0, 'Cancelled');
        setStrategy(''); clearStats();
        actions.innerHTML = `<button class="btn btn-ghost" onclick="convertAnother()">↩ Export another</button>`;
        clearSession();
      } else if (data.status === 'error') {
        clearInterval(pollTimer); clearInterval(elapsedTimer);
        showError(data.error || 'Export failed.'); clearSession();
      }
    } catch {
      clearInterval(pollTimer); clearInterval(elapsedTimer);
      showError('Lost connection to server.'); clearSession();
    }
  }, 800);
}

// ── ACTION BUTTONS ──
function showConvertingActions() {
  actions.innerHTML = `
    <button id="pauseBtn" class="btn btn-pause" onclick="togglePause()">⏸ Pause</button>
    <button class="btn btn-cancel" onclick="cancelConversion()">✕ Cancel</button>
  `;
}

function updatePauseBtn(paused) {
  const btn = document.getElementById('pauseBtn');
  if (!btn) return;
  btn.innerHTML = paused ? '▶ Resume' : '⏸ Pause';
  btn.classList.toggle('resumed', paused);
}

async function togglePause() {
  if (!currentJobId) return;
  try {
    const data = await fetch(`/pause/${currentJobId}/`, { method:'POST' }).then(r => r.json());
    isPaused = data.paused; updatePauseBtn(data.paused);
  } catch(e) { console.error('Pause failed', e); }
}

async function cancelConversion() {
  if (!currentJobId) return;
  if (!confirm('Cancel this export? Progress will be lost.')) return;
  clearInterval(pollTimer); clearInterval(elapsedTimer);
  try { await fetch(`/cancel/${currentJobId}/`, { method:'POST' }); } catch {}
  setStatus('cancelled', 0, 'Cancelled');
  setStrategy(''); clearStats();
  actions.innerHTML = `<button class="btn btn-ghost" onclick="convertAnother()">↩ Export another</button>`;
  clearSession();
}

function showComplete(filename, fileSize) {
  const captionsOn = document.getElementById('settingCaptions').checked;
  const captionStyle = document.getElementById('settingCaptionStyle').value;
  const captionNote = captionsOn && captionStyle === 'burned' ? ' · 🔥 Captions burned in' : '';
  document.getElementById('completeBanner').classList.add('active');
  document.getElementById('completeSubtitle').textContent = `${filename}${fileSize ? ' · ' + fileSize : ''}${captionNote} · Ready to download`;

const srtBtn = captionsOn && captionStyle === 'soft'
    ? `<a id="srtBtn" class="btn btn-ghost" href="#"
         style="opacity:0.5;cursor:not-allowed;pointer-events:none;">
         ⬇ Subtitles (.srt) &nbsp;<span id="srtStatus" style="font-size:9px">generating…</span>
       </a>`
    : '';

const isHard = captionsOn && captionStyle === 'burned';
  actions.innerHTML = `
    <a id="downloadBtn" class="btn btn-primary ${isHard ? 'btn-disabled' : ''}"
       href="${isHard ? '#' : `/download/${currentJobId}/`}"
       ${isHard ? '' : `download="${filename}"`}
       style="${isHard ? 'opacity:0.5;cursor:not-allowed;pointer-events:none;' : ''}">
      ${isHard ? '⏳ Preparing captions…' : '↓ Download ' + filename}
    </a>
    ${srtBtn}
    <button class="btn btn-ghost" onclick="convertAnother()">↩ Export another</button>
  `;
// Modal gets no action buttons — they're in the main UI
  document.getElementById('modalActions').innerHTML = '';
  if (captionsOn) pollForSrt();
  if (originalFileSize > 0 && fileSize) showSizeComparison(originalFileSize, fileSize);
  showThumbnail(filename, fileSize);
  openExportModal();
}

function pollForSrt() {
  // Inject caption progress bar into the actions area
  const existing = document.getElementById('captionProgressWrap');
  if (!existing) {
    const wrap = document.createElement('div');
    wrap.id = 'captionProgressWrap';
    wrap.style.cssText = `
      width:100%; margin-top:12px;
      font-family:var(--mono); font-size:9px; color:var(--muted);
    `;
    wrap.innerHTML = `
      <div id="captionStageLabel" style="
        display:flex; justify-content:space-between;
        margin-bottom:5px; letter-spacing:0.08em;
      ">
        <span id="captionStageText">🎙 Transcribing captions…</span>
        <span id="captionStagePct">0%</span>
      </div>
      <div style="
        height:4px; background:var(--border-mid);
        border-radius:1px; overflow:hidden;
      ">
        <div id="captionProgressFill" style="
          height:100%; width:0%;
          background:var(--accent);
          transition:width 0.4s ease;
          border-radius:1px;
        "></div>
      </div>
    `;
    const actionsEl = document.getElementById('actions');
    if (actionsEl) actionsEl.appendChild(wrap);
  }

  let fakeProgress = 0;
  let fakeInterval = null;

function startFakeProgress(targetPct, durationMs) {
    clearInterval(fakeInterval);
    const steps = 60;
    const increment = (targetPct - fakeProgress) / steps;
    const delay = durationMs / steps;
    fakeInterval = setInterval(() => {
      fakeProgress = Math.min(fakeProgress + increment, targetPct);
      updateCaptionBar(fakeProgress);
      if (fakeProgress >= targetPct) {
        clearInterval(fakeInterval);
        // Don't get stuck — pulse gently between target and target-5
        fakeInterval = setInterval(() => {
          const pulse = targetPct - 3 + Math.random() * 3;
          updateCaptionBar(pulse);
        }, 1500);
      }
    }, delay);
  }

  function updateCaptionBar(pct, label) {
    const fill = document.getElementById('captionProgressFill');
    const pctEl = document.getElementById('captionStagePct');
    const textEl = document.getElementById('captionStageText');
    if (fill) fill.style.width = Math.round(pct) + '%';
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    if (label && textEl) textEl.textContent = label;
  }

  // Start fake transcription progress — assume ~3 min for 10 min video
  startFakeProgress(95, 120000);

  const check = setInterval(async () => {
    try {
      const data = await fetch(`/status/${currentJobId}/`).then(r => r.json());
      const stage = data.caption_stage || '';
      const capPct = data.caption_progress || 0;

      if (stage === 'transcribing') {
        updateCaptionBar(fakeProgress, '🎙 Transcribing captions…');
      } else if (stage === 'transcribed') {
        clearInterval(fakeInterval);
        updateCaptionBar(100, '🎙 Transcription done');
      } else if (stage === 'burning') {
        clearInterval(fakeInterval);
        const realPct = data.burn_pct || 0;
        updateCaptionBar(realPct, `🔥 Burning captions… ${realPct}%`);
      } else if (stage === 'done') {
        clearInterval(fakeInterval);
        clearInterval(check);
        updateCaptionBar(100, '✅ Captions complete');

        // Unlock the download button now that hardsub is done
        const btn = document.getElementById('downloadBtn');
        if (btn) {
          btn.textContent = '↓ Download ' + (data.filename || filename);
          btn.href = `/download/${currentJobId}/`;
          btn.setAttribute('download', data.filename || filename);
          btn.style = '';
          btn.classList.remove('btn-disabled');
        }

        setTimeout(() => {
          const wrap = document.getElementById('captionProgressWrap');
          if (wrap) wrap.remove();
        }, 3000);
      }
if (data.srt_ready) {
    // For soft captions — unlock SRT download button
    document.querySelectorAll('#srtStatus').forEach(el => {
      el.textContent = '✓ ready';
      el.style.opacity = '1';
      el.style.color = 'var(--success)';
    });
    document.querySelectorAll('#srtBtn').forEach(el => {
      el.href = `/download-srt/${currentJobId}/`;
      el.setAttribute('download', '');
      el.style.opacity = '1';
      el.style.cursor = 'pointer';
      el.style.pointerEvents = 'auto';
    });

    // Caption preview overlay
    const captionStyle = document.getElementById('settingCaptionStyle').value;
    const videoEl = document.getElementById('videoPreviewEl');
    if (videoEl && captionStyle === 'burned') {
      fetch(`/download-srt/${currentJobId}/`)
        .then(r => r.text())
        .then(srtText => {
          const vttText = 'WEBVTT\n\n' + srtText.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
          const blob = new Blob([vttText], { type: 'text/vtt' });
          const vttUrl = URL.createObjectURL(blob);
          Array.from(videoEl.querySelectorAll('track')).forEach(t => t.remove());
          const track = document.createElement('track');
          track.kind = 'subtitles';
          track.label = 'Burned In Preview';
          track.srclang = 'en';
          track.src = vttUrl;
          track.default = true;
          videoEl.appendChild(track);
          videoEl.textTracks[0].mode = 'showing';
          const previewHeader = document.querySelector('.panel-split-left-header');
          if (previewHeader && !document.getElementById('captionPreviewBadge')) {
            const badge = document.createElement('span');
            badge.id = 'captionPreviewBadge';
            badge.textContent = '🔥 Caption Preview';
            badge.style.cssText = 'font-size:8px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#fff;background:var(--accent);padding:2px 7px;border-radius:2px;';
            previewHeader.appendChild(badge);
          }
        })
        .catch(() => {});
    }

    // ── KEY FIX: only stop polling for SOFT captions ──
    // For burned captions, keep polling so the burn stage can complete
    const captionStyleVal = document.getElementById('settingCaptionStyle').value;
    if (captionStyleVal !== 'burned') {
      clearInterval(check);
      clearInterval(fakeInterval);
      setTimeout(() => {
        const wrap = document.getElementById('captionProgressWrap');
        if (wrap) wrap.remove();
      }, 2000);
    }
    // For burned: let the existing stage === 'done' handler above take care of cleanup
  }
    } catch { clearInterval(check); clearInterval(fakeInterval); }
}, 1000); // poll every 1s instead of 2s for snappier feedback

  setTimeout(() => { clearInterval(check); clearInterval(fakeInterval); }, 600000); // 10 min timeout
}

function showSizeComparison(originalBytes, convertedSizeStr) {
  const parseSize = str => {
    const m = str.match(/([\d.]+)\s*(B|KB|MB|GB|TB)/i);
    if (!m) return 0;
    const mul = { b:1, kb:1024, mb:1024**2, gb:1024**3, tb:1024**4 };
    return parseFloat(m[1]) * (mul[m[2].toLowerCase()] || 1);
  };
  const convBytes = parseSize(convertedSizeStr);
  if (!convBytes) return;
  const maxBytes = Math.max(originalBytes, convBytes);
  const origPct  = Math.round((originalBytes / maxBytes) * 100);
  const convPct  = Math.round((convBytes / maxBytes) * 100);
  const saved    = originalBytes - convBytes;
  const savedPct = Math.round((saved / originalBytes) * 100);
  const el = document.getElementById('sizeCompare');
  el.innerHTML = `
    <div class="size-compare-label">File Size Comparison</div>
    <div class="size-compare-row">
      <div class="size-compare-name">Original</div>
      <div class="size-bar-track"><div class="size-bar-fill original" style="width:0%" data-target="${origPct}%"></div></div>
      <div class="size-bar-val">${humanSize(originalBytes)}</div>
    </div>
    <div class="size-compare-row">
      <div class="size-compare-name">Exported</div>
      <div class="size-bar-track"><div class="size-bar-fill converted" style="width:0%" data-target="${convPct}%"></div></div>
      <div class="size-bar-val">${convertedSizeStr}</div>
    </div>
    ${saved > 0
      ? `<div class="size-saving">▼ ${savedPct}% smaller — saved ${humanSize(saved)}</div>`
      : saved < 0
      ? `<div class="size-saving" style="color:var(--warning)">▲ ${Math.abs(savedPct)}% larger (higher quality settings)</div>`
      : `<div class="size-saving" style="color:var(--muted)">Same size</div>`}
  `;
  el.classList.add('active');
  requestAnimationFrame(() => {
    setTimeout(() => {
      el.querySelectorAll('.size-bar-fill').forEach(bar => { bar.style.width = bar.dataset.target; });
    }, 80);
  });
}

function showThumbnail(filename, fileSize) {
  const preview = document.getElementById('thumbPreview');
  const img     = document.getElementById('thumbImg');
  const loading = document.getElementById('thumbLoading');
  const footer  = document.getElementById('thumbFooter');
  preview.classList.add('active');
  loading.style.display = 'flex';
  img.classList.remove('loaded');
  img.src = '';
  setTimeout(() => {
    img.src = `/thumbnail/${currentJobId}/?t=${Date.now()}`;
    img.onload = () => {
      loading.style.display = 'none';
      img.classList.add('loaded');
      footer.innerHTML = `
        <span class="thumb-meta">Frame at 2s · <strong>${filename}</strong></span>
        ${fileSize ? `<span class="thumb-meta" style="margin-left:auto">Output: <strong>${fileSize}</strong></span>` : ''}
      `;
    };
    img.onerror = () => {
      setTimeout(() => {
        img.src = `/thumbnail/${currentJobId}/?t=${Date.now()}`;
        img.onload = () => { loading.style.display = 'none'; img.classList.add('loaded'); footer.innerHTML = `<span class="thumb-meta">Frame at 2s · <strong>${filename}</strong></span>`; };
        img.onerror = () => { preview.classList.remove('active'); };
      }, 1500);
    };
  }, 800);
}

function showError(msg) {
  setStatus('error', 0);
  errorBox.textContent = '⚠ ' + msg;
  errorBox.classList.add('active');
  actions.innerHTML = `<button class="btn btn-ghost" onclick="convertAnother()">↩ Try again</button>`;
}
function convertAnother() {
  closeExportModal();
  originalFileSize = 0;
  pendingFile = null;
  chatHistory = [];
  const previewWrap = document.getElementById('videoPreviewWrap');
  if (previewWrap) previewWrap.style.display = 'none';
  const videoEl = document.getElementById('videoPreviewEl');
  if (videoEl) { videoEl.src = ''; videoEl.load(); }
  if (videoObjectUrl) { URL.revokeObjectURL(videoObjectUrl); videoObjectUrl = null; }
  document.getElementById('aiTeaser').style.display = '';
  document.getElementById('aiPanel').style.display = 'none';
  if (currentJobId) { fetch(`/cleanup/${currentJobId}/`, { method:'POST' }); currentJobId = null; }
  clearInterval(pollTimer); clearInterval(elapsedTimer); clearSession();
  panel.classList.remove('active');
  document.getElementById('completeBanner')?.classList.remove('active');
  dropZone.style.display = '';
  document.getElementById('settingsCard').style.display = 'none';
  document.getElementById('step2Header').style.display = 'none';
  fileInput.value = ''; statusPct.style.color = '';
  errorBox.classList.remove('active');
  setStrategy(''); clearStats(); 
  isPaused = false; activePlatformLabel = null;
  userModifiedSettings = false;
  document.getElementById('settingCaptions').checked = false;
  document.getElementById('captionLabel').textContent = 'Off';
  document.getElementById('captionStyleGroup').style.display = 'none';
  // Clear any caption track from previous preview
  const prevVideo = document.getElementById('videoPreviewEl');
  if (prevVideo) Array.from(prevVideo.querySelectorAll('track')).forEach(t => t.remove());
  const badge = document.getElementById('captionPreviewBadge');
  if (badge) badge.remove();
  // Restore original quality/codec labels
  const qualitySelect = document.getElementById('settingQuality');
  Array.from(qualitySelect.options).forEach(opt => { if (opt._originalText) opt.text = opt._originalText; });
  const codecSelect = document.getElementById('settingCodec');
  Array.from(codecSelect.options).forEach(opt => { if (opt._originalText) opt.text = opt._originalText; });
  document.getElementById('thumbPreview').classList.remove('active');
  document.getElementById('sizeCompare').classList.remove('active');
}

// ── HAMBURGER ──
function toggleMenu() {
  const btn  = document.getElementById('hamburger');
  const menu = document.getElementById('mobileMenu');
  btn.classList.toggle('open');
  menu.classList.toggle('open');
}
document.addEventListener('click', e => {
  const btn  = document.getElementById('hamburger');
  const menu = document.getElementById('mobileMenu');
  if (!btn.contains(e.target) && !menu.contains(e.target)) {
    btn.classList.remove('open');
    menu.classList.remove('open');
  }
});

// ── LIVE CREDIT COUNTER ──
function refreshCredits() {
  fetch('/credits-status/')
    .then(r => r.json())
    .then(data => {
      const badge = document.querySelector('.credit-badge');
      if (badge) {
        if (data.credits > 0) {
          badge.className = 'credit-badge paid';
          badge.textContent = `⚡ ${data.credits} credits`;
        } else if (data.free_remaining > 0) {
          badge.className = 'credit-badge free';
          badge.textContent = `${data.free_remaining} exports left`;
        } else {
          badge.className = 'credit-badge free';
          badge.textContent = '↻ Resets next month';
        }
      }
      const mobileBadge = document.querySelector('.mobile-credits');
      if (mobileBadge) {
        if (data.credits > 0) {
          mobileBadge.textContent = `⚡ ${data.credits} credits`;
        } else if (data.free_remaining > 0) {
          mobileBadge.textContent = `${data.free_remaining} exports left`;
        } else {
          mobileBadge.textContent = '↻ Resets next month';
        }
      }
    })
    .catch(() => {});
}
setInterval(refreshCredits, 5000);

// ── LOGIN MODAL ──
function showLoginPrompt() {
  const modal = document.getElementById('loginModal');
  modal.style.display = 'flex';
  modal.addEventListener('click', e => {
    if (e.target === modal) closeLoginModal();
  }, { once: true });
}
function closeLoginModal() {
  document.getElementById('loginModal').style.display = 'none';
}

// ══════════════════════════════════════
//  AI CHAT
// ══════════════════════════════════════
function appendMsg(role, text) {
  const box = document.getElementById('chatMessages');
  const wrap = document.createElement('div');
  wrap.className = `chat-msg ${role}`;
  wrap.innerHTML = `
    <div class="chat-sender">${role === 'user' ? 'You' : 'AI'}</div>
    <div class="chat-bubble">${text}</div>
  `;
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return wrap;
}

function showTyping() {
  const box = document.getElementById('chatMessages');
  const el = document.createElement('div');
  el.className = 'chat-msg ai';
  el.id = 'typingIndicator';
  el.innerHTML = `
    <div class="chat-sender">AI</div>
    <div class="typing-dots"><span></span><span></span><span></span></div>
  `;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function removeTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

function sendQuick(text) {
  document.getElementById('aiInput').value = text;
  sendChat();
}

async function sendChat() {
  const input = document.getElementById('aiInput');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  // Hide quick chips after first message
  const chips = document.getElementById('quickChips');
  if (chips) chips.style.display = 'none';

  chatHistory.push({ role: 'user', content: msg });
  appendMsg('user', msg);
  showTyping();

  try {
    const resp = await fetch('/ai-suggest/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({
        mode: 'chat',
        message: msg,
        history: chatHistory,
        fileinfo: pendingFile ? {
          name: pendingFile.name,
          size: humanSize(pendingFile.size),
          input_ext: pendingFile.name.split('.').pop().toLowerCase(),
          current_format: outputFormat.value,
          current_resolution: document.getElementById('settingRes').value,
          current_quality: document.getElementById('settingQuality').value,
          current_codec: document.getElementById('settingCodec').value,
        } : { current_format: outputFormat.value }
      })
    });
    const data = await resp.json();
    removeTyping();
    if (data.error) { appendMsg('ai', '⚠ ' + data.error); return; }
    chatHistory.push({ role: 'assistant', content: data.explanation || '' });
    activePlatformLabel = null;
    document.querySelectorAll('.platform-chip').forEach(btn => btn.classList.remove('active'));
    applyAISettings(data);
    appendMsg('ai', '✦ ' + (data.explanation || 'Settings applied.'));
  } catch {
    removeTyping();
    appendMsg('ai', '⚠ AI unavailable, try again.');
  }
}

// ── SMART SUGGEST ON FILE DROP ──
async function suggestForFile(file, probeData) {
  const inputExt = file.name.split('.').pop().toLowerCase();

  // Hide quick chips, show file message in chat
  const chips = document.getElementById('quickChips');
  if (chips) chips.style.display = 'none';
  appendMsg('user', `📎 ${file.name} (${humanSize(file.size)})`);
  showTyping();

  try {
    const resp = await fetch('/ai-suggest/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({
        mode: 'suggest',
        fileinfo: {
          name: file.name,
          size: humanSize(file.size),
          duration: probeData?.duration || 'unknown',
          vcodec: probeData?.vcodec || 'unknown',
          acodec: probeData?.acodec || 'unknown',
          current_format: outputFormat.value,
          input_ext: inputExt,
          active_platform: activePlatformLabel || '',
        }
      })
    });
    const data = await resp.json();
    removeTyping();
    if (data.error) {
      appendMsg('ai', 'File loaded! Where are you posting this?');
      return;
    }
 if (activePlatformLabel || userModifiedSettings) {
      appendMsg('ai', `✦ Got it — ${humanSize(file.size)} file loaded. Settings are locked to your current selection. Hit Export Now when ready.`);
    } else {
      applyAISettings(data);
      appendMsg('ai', '✦ ' + (data.explanation || 'Settings auto-selected.'));
    }
  } catch {
    removeTyping();
    appendMsg('ai', 'File loaded! Where are you posting this?');
  }
}

function applyAISettings(data) {
  // Always apply captions regardless of anything
  if (typeof data.captions === 'boolean') {
    const captionCheckbox = document.getElementById('settingCaptions');
    captionCheckbox.checked = data.captions;
    document.getElementById('captionLabel').textContent = data.captions ? 'On' : 'Off';
    document.getElementById('captionStyleGroup').style.display = data.captions ? '' : 'none';
  }

  // If platform preset is active, only allow filename change
  if (activePlatformLabel) {
    if (data.filename && data.filename.trim()) {
      const fnInput = document.getElementById('outputFilename');
      if (fnInput) fnInput.value = data.filename.trim();
    }
    return;
  }

  // Apply all settings
  if (data.format) {
    outputFormat.value = data.format;
    highlightSelectedFormat();
    document.getElementById('outputFilenameExt').textContent = '.' + data.format;
  }
  if (data.resolution) document.getElementById('settingRes').value     = data.resolution;
  if (data.quality)    document.getElementById('settingQuality').value = data.quality;
  if (data.codec)      document.getElementById('settingCodec').value   = data.codec;
  if (data.filename && data.filename.trim()) {
    const fnInput = document.getElementById('outputFilename');
    if (fnInput) fnInput.value = data.filename.trim();
  }

  // Lock settings AFTER applying so file drop won't overwrite them
  userModifiedSettings = true;
}

function getCookie(name) {
  const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
  return v ? v[2] : null;
}

// ── BROWSE DROPDOWN ──
// ── BROWSE DROPDOWN ──
function toggleBrowseMenu(e) {
  e.stopPropagation();
  const dd = document.getElementById('browseDropdown');
  const isOpen = dd.classList.contains('open');
  dd.classList.toggle('open', !isOpen);
}

function closeBrowseMenu() {
  const dd = document.getElementById('browseDropdown');
  if (dd) dd.classList.remove('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
  if (!e.target.closest('.btn-browse-wrap')) closeBrowseMenu();
});
// ── GOOGLE DRIVE PICKER ──
function openDrivePicker() {
  const input = document.getElementById('driveLinkInput');
  const modal = document.getElementById('driveModal');
  if (input) input.value = '';
  if (modal) modal.style.display = 'flex';
}

function closeDriveModal() {
  document.getElementById('driveModal').style.display = 'none';
}

async function importFromDrive() {
  const url = document.getElementById('driveLinkInput').value.trim();
  if (!url) return;

  // Extract file ID from various Drive URL formats
  const match = url.match(/\/d\/([a-zA-Z0-9_-]+)/) || url.match(/id=([a-zA-Z0-9_-]+)/);
  if (!match) {
    alert('Invalid Google Drive link. Make sure it\'s a shareable file link.');
    return;
  }

  closeDriveModal();
  const fileId = match[1];

  // Show loading state
  dropZone.style.display = 'none';
  panel.classList.add('active');
  document.getElementById('settingsCard').style.display = '';
  panelName.textContent = 'Importing from Drive…';
  panelSize.textContent = '—';
  convTag.textContent   = 'DRIVE';
  setStatus('queued', 0, 'Importing…');
  actions.innerHTML = `<span style="font-family:var(--mono);font-size:10px;color:var(--muted)">⏳ Downloading from Google Drive…</span>`;

  try {
    const resp = await fetch('/import-drive/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
      body: JSON.stringify({ file_id: fileId, output_format: outputFormat.value })
    });
    const data = await resp.json();

    if (resp.status === 401) { showLoginPrompt(); panel.classList.remove('active'); dropZone.style.display = ''; return; }
    if (data.error) { showError(data.error); return; }

    panelName.textContent = data.filename || 'Drive file';
    panelSize.textContent = data.file_size || '—';
    currentJobId = data.job_id;
    startTime = Date.now();
    saveSession(currentJobId, data.filename || 'Drive file', outputFormat.value.toUpperCase());
    elapsedTimer = setInterval(() => {
      if (!isPaused) statElapsed.textContent = `Elapsed: ${fmtElapsed(Date.now() - startTime)}`;
    }, 1000);
    setStatus('converting', 0);
    showConvertingActions();
    pollStatus();
  } catch {
    showError('Failed to import from Google Drive.');
  }
}
// ====================== HISTORY MODAL ======================

async function openHistoryModal() {
  const modal = document.getElementById('historyModal');
  const content = document.getElementById('historyContent');
  
  modal.style.display = 'flex';
  content.innerHTML = `
    <div style="text-align:center; padding:80px 20px; color:var(--muted);">
      Loading your export history...
    </div>`;

  try {
    const res = await fetch('/history/');
    
    if (res.status === 401) {
      closeHistoryModal();
      showLoginPrompt();
      return;
    }

    if (!res.ok) throw new Error('Failed to load');

    const data = await res.json();
    const records = data.records || [];

    if (records.length === 0) {
      content.innerHTML = `
        <div style="text-align:center; padding:100px 20px; color:var(--muted);">
          No exports yet.<br>
          <small style="font-size:9px;">Your completed exports will appear here.</small>
        </div>`;
      return;
    }

    let html = `
      <table style="width:100%; border-collapse:collapse; font-size:10px;">
        <thead>
          <tr style="background:var(--surface2);">
            <th style="padding:10px; text-align:left; border-bottom:1px solid var(--border-mid);">File</th>
            <th style="padding:10px; text-align:center; border-bottom:1px solid var(--border-mid);">Input</th>
            <th style="padding:10px; text-align:center; border-bottom:1px solid var(--border-mid);">Output</th>
            <th style="padding:10px; text-align:right; border-bottom:1px solid var(--border-mid);">Size</th>
            <th style="padding:10px; text-align:center; border-bottom:1px solid var(--border-mid);">Status</th>
            <th style="padding:10px; text-align:right; border-bottom:1px solid var(--border-mid);">Date</th>
          </tr>
        </thead>
        <tbody>`;

    records.forEach(r => {
      const statusClass = r.status === 'done' ? 'color:var(--success);' : 
                         r.status === 'error' ? 'color:var(--danger);' : '';
      
      html += `
        <tr style="border-bottom:1px solid var(--border-mid);">
          <td style="padding:10px;">${r.input_name}</td>
          <td style="padding:10px; text-align:center; text-transform:uppercase;">${r.input_ext}</td>
          <td style="padding:10px; text-align:center; color:var(--accent);">${r.output_format}</td>
          <td style="padding:10px; text-align:right;">${r.file_size ? (r.file_size / (1024*1024)).toFixed(1) + ' MB' : '—'}</td>
          <td style="padding:10px; text-align:center; ${statusClass}">${r.status.toUpperCase()}</td>
          <td style="padding:10px; text-align:right; color:var(--text-mid);">${r.date}</td>
        </tr>`;
    });

    html += `</tbody></table>`;
    
    content.innerHTML = html;

  } catch (e) {
    content.innerHTML = `
      <div style="color:var(--danger); text-align:center; padding:60px 20px;">
        ⚠ Could not load history.<br>
        <small style="font-size:9px;">Please try again later.</small>
      </div>`;
  }
}

function closeHistoryModal() {
  const modal = document.getElementById('historyModal');
  if (modal) modal.style.display = 'none';
}

// Close when clicking outside
document.getElementById('historyModal').addEventListener('click', function(e) {
  if (e.target === this) closeHistoryModal();
});

// ====================== LOGOUT CONFIRMATION ======================

// ====================== LOGOUT CONFIRMATION ======================

function confirmLogout() {
  const modal = document.createElement('div');
  modal.id = 'logoutConfirmModal';
  modal.style.cssText = `
    position:fixed; inset:0; z-index:11000;
    background:rgba(0,0,0,0.85);
    display:flex; align-items:center; justify-content:center;
    padding:20px;
    animation:fadeIn 0.2s ease;
  `;

  // Get username and credits from the DOM (already rendered by Django)
  const username = document.querySelector('.user-pill')?.textContent?.trim()?.replace('👤','')?.trim() || '';
  const creditBadge = document.querySelector('.credit-badge');
  const creditHTML = creditBadge
    ? `<div style="
        display:flex; align-items:center; gap:10px;
        padding:10px 14px;
        background:var(--surface2);
        border:1px solid var(--border-mid);
        border-left:3px solid var(--accent);
        margin-bottom:24px;
      ">
        <span style="font-size:14px;flex-shrink:0;">👤</span>
        <span style="font-family:var(--mono);font-size:10px;font-weight:700;color:var(--text-mid);flex:1;">${username}</span>
        <span style="
          font-family:var(--mono);font-size:8px;font-weight:700;
          letter-spacing:0.08em;text-transform:uppercase;
          color:var(--accent);border:1px dashed rgba(102,0,255,0.4);
          background:var(--accent-soft);padding:3px 7px;
        ">${creditBadge.textContent.trim()}</span>
      </div>`
    : '';

  modal.innerHTML = `
    <div style="
      background:var(--surface);
      border:1px solid var(--border-mid);
      border-top:3px solid var(--danger);
      padding:32px 28px;
      max-width:380px; width:100%;
      position:relative;
    ">
      <div style="
        font-family:var(--mono);font-size:9px;font-weight:700;
        letter-spacing:0.2em;text-transform:uppercase;
        color:var(--danger);margin-bottom:12px;
      ">Sign Out</div>

      <div style="
        font-family:var(--sans);font-weight:800;
        font-size:clamp(20px,5vw,24px);
        letter-spacing:-0.03em;color:var(--text);
        margin-bottom:8px;line-height:1.1;
      ">See you later?</div>

      <div style="
        font-family:var(--mono);font-size:10px;
        color:var(--muted);margin-bottom:24px;line-height:1.7;
      ">You'll need to sign back in to access your exports and history.</div>

      ${creditHTML}

      <div style="display:flex;flex-direction:column;gap:8px;">
        <button onclick="performLogout()" style="
          font-family:var(--mono);font-size:10px;font-weight:700;
          letter-spacing:0.12em;text-transform:uppercase;
          padding:13px 20px;background:var(--danger);color:#fff;
          border:1px solid var(--danger);cursor:pointer;
          display:flex;align-items:center;justify-content:center;gap:8px;
          transition:background 0.15s; width:100%;
        " onmouseover="this.style.background='#aa1a1a'" onmouseout="this.style.background='var(--danger)'">
          → Yes, Sign Out
        </button>

        <button onclick="closeLogoutConfirm()" style="
          font-family:var(--mono);font-size:10px;font-weight:700;
          letter-spacing:0.12em;text-transform:uppercase;
          padding:13px 20px;color:var(--text-mid);
          border:1px solid var(--border-bright);background:var(--surface2);
          cursor:pointer;transition:all 0.15s; width:100%;
        " onmouseover="this.style.borderColor='var(--text-mid)'" onmouseout="this.style.borderColor='var(--border-bright)'">
          ✕ Stay Signed In
        </button>

        <button onclick="closeLogoutConfirm()" style="
          font-family:var(--mono);font-size:9px;font-weight:700;
          letter-spacing:0.1em;text-transform:uppercase;
          padding:9px;color:var(--muted);
          border:1px solid transparent;background:transparent;
          cursor:pointer; width:100%;
        ">Cancel</button>
      </div>
    </div>
  `;

  modal.addEventListener('click', e => { if (e.target === modal) closeLogoutConfirm(); });
  document.body.appendChild(modal);
}

function closeLogoutConfirm() {
  const modal = document.getElementById('logoutConfirmModal');
  if (modal) modal.remove();
}

function performLogout() {
  window.location.href = '/logout/';
}

function closeLogoutConfirm() {
  const modal = document.getElementById('logoutConfirmModal');
  if (modal) modal.remove();
}

function performLogout() {
  window.location.href = '/logout/';
}

function openExportModal() {
  const modal = document.getElementById('exportCompleteModal');
  modal.style.display = 'flex';
}
function closeExportModal() {
  document.getElementById('exportCompleteModal').style.display = 'none';
}