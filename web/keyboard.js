/* The device keyboard edits only these fields; a submit sends one complete request.
   No per-keystroke requests, query logging, browser storage or automatic retries. */
const youtubeSearchForm = document.querySelector('#youtube-search-form');
const youtubeQuery = document.querySelector('#youtube-query');
const youtubeUrlForm = document.querySelector('#youtube-url-form');
const youtubeUrl = document.querySelector('#youtube-url');
const seekSlider = document.querySelector('#youtube-seek-slider');
const seekTimeInput = document.querySelector('#youtube-seek-time');
const seekReadout = document.querySelector('#youtube-seek-readout');
const seekSubmit = document.querySelector('#youtube-seek-submit');
const seekForm = document.querySelector('#youtube-seek-form');
const previewWrap = document.querySelector('#youtube-preview-wrap');
const previewCanvas = document.querySelector('#youtube-preview-canvas');
const previewCtx = previewCanvas ? previewCanvas.getContext('2d') : null;
const previewTitle = document.querySelector('#youtube-preview-title');
const previewBadge = document.querySelector('#youtube-preview-badge');
const previewEmpty = document.querySelector('#youtube-preview-empty');
const previewToggle = document.querySelector('#youtube-preview-toggle');

const isYoutubeLink = value => /^(https?:\/\/)?((www|m|music)\.)?(youtube\.com|youtu\.be)\//i.test(value.trim());

function extractYoutubeId(value) {
 const raw = (value || '').trim();
 if (!isYoutubeLink(raw)) return null;
 try {
  const normalized = /^https?:\/\//i.test(raw) ? raw : 'https://' + raw;
  const u = new URL(normalized);
  const host = u.hostname.toLowerCase();
  let id = '';
  if (host === 'youtu.be' || host === 'www.youtu.be') {
   id = u.pathname.split('/').filter(Boolean)[0] || '';
  } else {
   id = u.searchParams.get('v') || '';
   if (!id) {
    const parts = u.pathname.split('/').filter(Boolean);
    if (parts.length >= 2 && ['shorts', 'live', 'embed', 'v'].includes(parts[0])) id = parts[1];
   }
  }
  return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
 } catch {
  return null;
 }
}

function formatSeekTime(totalSeconds) {
 const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
 const h = Math.floor(sec / 3600);
 const m = Math.floor((sec % 3600) / 60);
 const s = sec % 60;
 if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
 return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function parseSeekTime(text) {
 const raw = (text || '').trim();
 if (!raw) return null;
 if (/^\d+$/.test(raw)) {
  const val = Number(raw);
  return val >= 0 && val <= 86400 ? val : null;
 }
 const parts = raw.split(':').map(p => p.trim());
 if (parts.length < 2 || parts.length > 3 || parts.some(p => !/^\d+$/.test(p))) return null;
 const nums = parts.map(Number);
 let sec = 0;
 if (nums.length === 2) {
  if (nums[1] >= 60) return null;
  sec = nums[0] * 60 + nums[1];
 } else {
  if (nums[1] >= 60 || nums[2] >= 60) return null;
  sec = nums[0] * 3600 + nums[1] * 60 + nums[2];
 }
 return sec >= 0 && sec <= 86400 ? sec : null;
}

let currentPreviewVideoId = null;
let previewDuration = 0;
let storyboardSpec = null;
const sheetCache = new Map();
let pendingDraw = null;
let lastPaintedTile = '';
let userScrubUntil = 0;
let liveBaseSec = null;
let liveBaseAt = 0;
let liveState = null;

function drawFullImage(img) {
 if (!previewCanvas || !previewCtx || !img.naturalWidth || !img.naturalHeight) return;
 previewCtx.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
 const scale = Math.max(previewCanvas.width / img.naturalWidth, previewCanvas.height / img.naturalHeight);
 const dw = img.naturalWidth * scale;
 const dh = img.naturalHeight * scale;
 previewCtx.drawImage(img, (previewCanvas.width - dw) / 2, (previewCanvas.height - dh) / 2, dw, dh);
}

function drawStoryboardTile(img, sb, col, row, tileKey = '') {
 if (!previewCanvas || !previewCtx || !img.naturalWidth || !img.naturalHeight) return;
 const sx = col * sb.width;
 const sy = row * sb.height;
 if (sx + sb.width > img.naturalWidth + 1 || sy + sb.height > img.naturalHeight + 1) return;
 previewCtx.clearRect(0, 0, previewCanvas.width, previewCanvas.height);
 previewCtx.drawImage(img, sx, sy, sb.width, sb.height, 0, 0, previewCanvas.width, previewCanvas.height);
 if (tileKey) lastPaintedTile = tileKey;
}

function getSheetImage(videoId, sb, sheetIdx) {
 const key = `${videoId}:${sheetIdx}`;
 let entry = sheetCache.get(key);
 if (entry) return entry;
 const img = new Image();
 img.decoding = 'async';
 entry = {img, loaded: false, failed: false};
 sheetCache.set(key, entry);
 img.onload = () => {
  entry.loaded = true;
  if (pendingDraw && pendingDraw.videoId === videoId && pendingDraw.sheetIdx === sheetIdx) {
   drawStoryboardTile(img, sb, pendingDraw.col, pendingDraw.row, pendingDraw.tileKey);
  }
 };
 img.onerror = () => {
  entry.failed = true;
 };
 img.src = sb.sheet_url_template.replace('{M}', String(sheetIdx));
 return entry;
}

function updateSeekUI(seconds, updateInput = true) {
 if (!seekSlider) return;
 const maxVal = Number(seekSlider.max) || 3600;
 const clamped = Math.max(0, Math.min(86400, Math.floor(Number(seconds) || 0)));
 if (clamped > maxVal) seekSlider.max = String(clamped);
 seekSlider.value = String(clamped);
 const formatted = formatSeekTime(clamped);
 const durFormatted = previewDuration > 0 ? formatSeekTime(previewDuration) : '--:--';
 if (seekReadout) seekReadout.textContent = `${formatted} / ${durFormatted}`;
 if (previewBadge) previewBadge.textContent = formatted;
 if (seekSubmit) seekSubmit.textContent = `Vai a ${formatted} sulla TV`;
 if (updateInput && seekTimeInput) seekTimeInput.value = formatted;
}

function seekPreviewFrame(seconds, force = false) {
 const target = Math.max(0, Math.floor(Number(seconds) || 0));
 if (previewBadge) previewBadge.textContent = formatSeekTime(target);
 if (!currentPreviewVideoId || !storyboardSpec || !previewCanvas || previewWrap?.hidden) return;
 const sb = storyboardSpec;
 const frameIdx = sb.interval_ms > 0
  ? Math.min(sb.count - 1, Math.max(0, Math.floor((target * 1000) / sb.interval_ms)))
  : (previewDuration > 0 ? Math.min(sb.count - 1, Math.max(0, Math.floor((target / previewDuration) * sb.count))) : 0);
 const perSheet = Math.max(1, sb.cols * sb.rows);
 const totalSheets = Math.max(1, Math.ceil(sb.count / perSheet));
 const sheetIdx = Math.min(totalSheets - 1, Math.floor(frameIdx / perSheet));
 const cellIdx = frameIdx % perSheet;
 const col = cellIdx % sb.cols;
 const row = Math.floor(cellIdx / sb.cols);
 const tileKey = `${currentPreviewVideoId}:${sheetIdx}:${col}:${row}`;
 if (!force && tileKey === lastPaintedTile) return;

 pendingDraw = {videoId: currentPreviewVideoId, sheetIdx, col, row, tileKey};
 const entry = getSheetImage(currentPreviewVideoId, sb, sheetIdx);
 if (entry.loaded && !entry.failed) {
  drawStoryboardTile(entry.img, sb, col, row, tileKey);
 }
 // Preload adjacent sheets for instantaneous scrubbing.
 if (sheetIdx + 1 < totalSheets) getSheetImage(currentPreviewVideoId, sb, sheetIdx + 1);
 if (sheetIdx - 1 >= 0) getSheetImage(currentPreviewVideoId, sb, sheetIdx - 1);
}

async function loadStoryboard(videoId) {
 try {
  const resp = await fetch(`/api/youtube/storyboard?v=${encodeURIComponent(videoId)}`, {
   headers: {'X-Remote-Key': controlKey},
   signal: AbortSignal.timeout(7000),
  });
  if (!resp.ok) return;
  const data = await resp.json();
  if (videoId !== currentPreviewVideoId) return;
  if (previewTitle) previewTitle.textContent = data.title || '';
  if (typeof data.duration === 'number' && data.duration > 0) {
   previewDuration = Math.round(data.duration);
   if (seekSlider) seekSlider.max = String(previewDuration);
   updateSeekUI(Number(seekSlider?.value) || 0, false);
  }
  if (data.storyboard && previewCanvas) {
   storyboardSpec = data.storyboard;
   previewCanvas.width = storyboardSpec.width;
   previewCanvas.height = storyboardSpec.height;
   lastPaintedTile = '';
   seekPreviewFrame(Number(seekSlider?.value) || 0, true);
  }
 } catch {}
}

function syncYoutubePreview(videoId) {
 if (!videoId || !/^[A-Za-z0-9_-]{11}$/.test(videoId) || videoId === currentPreviewVideoId) return;
 currentPreviewVideoId = videoId;
 previewDuration = 0;
 storyboardSpec = null;
 pendingDraw = null;
 lastPaintedTile = '';
 liveBaseSec = null;
 liveBaseAt = 0;
 liveState = null;
 sheetCache.clear();
 if (previewTitle) previewTitle.textContent = '';
 if (previewCanvas) {
  previewCanvas.width = 320;
  previewCanvas.height = 180;
  previewCanvas.hidden = false;
 }
 if (previewBadge) previewBadge.hidden = false;
 if (previewEmpty) previewEmpty.hidden = true;
 const poster = new Image();
 poster.decoding = 'async';
 poster.onload = () => {
  if (videoId === currentPreviewVideoId && !storyboardSpec) drawFullImage(poster);
 };
 poster.src = `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
 updateSeekUI(Number(seekSlider?.value) || 0, true);
 loadStoryboard(videoId);
}
window.syncYoutubePreview = syncYoutubePreview;

function currentLiveSeconds() {
 if (liveBaseSec === null) return null;
 if (liveState !== 1) return liveBaseSec;
 const elapsed = Math.max(0, Math.floor((Date.now() - liveBaseAt) / 1000));
 const total = liveBaseSec + elapsed;
 return previewDuration > 0 ? Math.min(previewDuration, total) : total;
}

function tickLiveYoutubeTiming() {
 if (document.hidden || liveBaseSec === null) return;
 const isEditing = Date.now() < userScrubUntil || document.activeElement === seekTimeInput;
 if (isEditing) return;
 const cur = currentLiveSeconds();
 if (cur === null) return;
 updateSeekUI(cur, true);
 seekPreviewFrame(cur);
}
setInterval(tickLiveYoutubeTiming, 1000);

function syncYoutubeProgress(progress) {
 if (!progress || typeof progress !== 'object') return;
 if (progress.video_id) syncYoutubePreview(progress.video_id);
 if (typeof progress.duration === 'number' && progress.duration > 0) {
  const dur = Math.round(progress.duration);
  if (dur !== previewDuration) {
   previewDuration = dur;
   if (seekSlider) seekSlider.max = String(dur);
  }
 }
 if (typeof progress.state === 'number') liveState = progress.state;
 if (typeof progress.current_time === 'number' && progress.current_time >= 0) {
  liveBaseSec = Math.round(progress.current_time);
  liveBaseAt = Date.now();
  tickLiveYoutubeTiming();
 } else if (previewDuration > 0) {
  updateSeekUI(Number(seekSlider?.value) || 0, false);
 }
}
window.syncYoutubeProgress = syncYoutubeProgress;

if (previewToggle && previewWrap) {
 previewToggle.addEventListener('click', () => {
  const hiding = !previewWrap.hidden;
  previewWrap.hidden = hiding;
  previewToggle.setAttribute('aria-expanded', String(!hiding));
  previewToggle.textContent = hiding ? 'Mostra anteprima' : 'Nascondi anteprima';
  if (!hiding) seekPreviewFrame(Number(seekSlider?.value) || 0, true);
 });
}

if (seekSlider) {
 seekSlider.addEventListener('input', () => {
  userScrubUntil = Date.now() + 12000;
  const sec = Number(seekSlider.value) || 0;
  if (seekTimeInput) seekTimeInput.setCustomValidity('');
  updateSeekUI(sec, true);
  seekPreviewFrame(sec);
 });
}

document.querySelectorAll('[data-yt-step]').forEach(btn => {
 btn.addEventListener('click', () => {
  if (!seekSlider) return;
  userScrubUntil = Date.now() + 12000;
  const delta = Number(btn.dataset.ytStep) || 0;
  const maxVal = previewDuration > 0 ? previewDuration : (Number(seekSlider.max) || 3600);
  const next = Math.max(0, Math.min(maxVal, (Number(seekSlider.value) || 0) + delta));
  if (seekTimeInput) seekTimeInput.setCustomValidity('');
  updateSeekUI(next, true);
  seekPreviewFrame(next);
 });
});

if (seekTimeInput) {
 seekTimeInput.addEventListener('input', () => {
  userScrubUntil = Date.now() + 12000;
  seekTimeInput.setCustomValidity('');
  const parsed = parseSeekTime(seekTimeInput.value);
  if (parsed !== null) {
   updateSeekUI(parsed, false);
   seekPreviewFrame(parsed);
  }
 });
 seekTimeInput.addEventListener('blur', () => {
  const parsed = parseSeekTime(seekTimeInput.value);
  if (parsed !== null) updateSeekUI(parsed, true);
 });
}

if (seekForm) {
 seekForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (pending) {
   status.textContent = 'Attendi il termine del comando in corso, poi riprova.';
   return;
  }
  const parsed = seekTimeInput ? parseSeekTime(seekTimeInput.value) : Number(seekSlider?.value || 0);
  if (parsed === null) {
   if (seekTimeInput) {
    seekTimeInput.setCustomValidity('Inserisci un orario valido in formato MM:SS o HH:MM:SS.');
    seekTimeInput.reportValidity();
   }
   return;
  }
  userScrubUntil = 0;
  liveBaseSec = parsed;
  liveBaseAt = Date.now();
  updateSeekUI(parsed, true);
  seekPreviewFrame(parsed);
  await command({youtube_seek: parsed});
 });
}

if (youtubeUrlForm && youtubeUrl) {
 youtubeUrlForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (pending) {
   status.textContent = 'Attendi il termine del comando in corso, poi premi Riproduci.';
   return;
  }
  const url = youtubeUrl.value.trim();
  const vid = extractYoutubeId(url);
  if (!url || !vid) {
   youtubeUrl.setCustomValidity('Inserisci un link YouTube valido (es. https://youtu.be/... o youtube.com/watch?v=...).');
   youtubeUrl.reportValidity();
   return;
  }
  syncYoutubePreview(vid);
  const result = await command({youtube_url: url});
  if (result) youtubeUrl.value = '';
 });
 youtubeUrl.addEventListener('input', () => {
  youtubeUrl.setCustomValidity('');
  const vid = extractYoutubeId(youtubeUrl.value);
  if (vid) syncYoutubePreview(vid);
 });
}

youtubeSearchForm.addEventListener('submit', async event => {
 event.preventDefault();
 if (pending) {
  status.textContent = 'Attendi il termine del comando in corso, poi premi Cerca.';
  return;
 }
 const query = youtubeQuery.value;
 if (!query.trim()) {
  youtubeQuery.setCustomValidity('Scrivi cosa vuoi cercare.');
  youtubeQuery.reportValidity();
  return;
 }
 const vid = extractYoutubeId(query);
 if (vid) {
  syncYoutubePreview(vid);
  const result = await command({youtube_url: query.trim()});
  if (result) youtubeQuery.value = '';
  return;
 }
 await command({youtube_search: query});
});
youtubeQuery.addEventListener('input', () => youtubeQuery.setCustomValidity(''));
