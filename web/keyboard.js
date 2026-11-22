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
const previewFrame = document.querySelector('#youtube-preview-frame');
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
let initialPaused = false;
let seekPreviewTimer = null;
let pausePreviewTimer = null;
let lastPreviewSeekAt = 0;
let userScrubUntil = 0;
let lastAutoSyncedSec = -1;

function ytCommand(func, args = []) {
 if (!previewFrame || !previewFrame.contentWindow || !currentPreviewVideoId) return;
 try {
  previewFrame.contentWindow.postMessage(JSON.stringify({event: 'command', func, args}), '*');
 } catch {}
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
 if (seekSubmit) seekSubmit.textContent = `Vai a ${formatted} sulla TV`;
 if (updateInput && seekTimeInput) seekTimeInput.value = formatted;
}

function seekPreviewFrame(seconds) {
 if (!currentPreviewVideoId || !previewFrame || previewWrap?.hidden) return;
 const target = Math.max(0, Math.floor(Number(seconds) || 0));
 const now = performance.now();
 const fire = () => {
  lastPreviewSeekAt = performance.now();
  ytCommand('mute');
  ytCommand('seekTo', [target, true]);
  clearTimeout(pausePreviewTimer);
  pausePreviewTimer = setTimeout(() => ytCommand('pauseVideo'), 240);
 };
 clearTimeout(seekPreviewTimer);
 if (now - lastPreviewSeekAt >= 180) fire();
 else seekPreviewTimer = setTimeout(fire, 180);
}

function syncYoutubePreview(videoId) {
 if (!videoId || !/^[A-Za-z0-9_-]{11}$/.test(videoId) || videoId === currentPreviewVideoId) return;
 currentPreviewVideoId = videoId;
 previewDuration = 0;
 initialPaused = false;
 lastAutoSyncedSec = -1;
 if (previewFrame) {
  const origin = encodeURIComponent(location.origin);
  previewFrame.src = `https://www.youtube.com/embed/${videoId}?enablejsapi=1&mute=1&autoplay=1&controls=0&rel=0&playsinline=1&origin=${origin}`;
  previewFrame.hidden = false;
 }
 if (previewEmpty) previewEmpty.hidden = true;
 updateSeekUI(Number(seekSlider?.value) || 0, true);
}
window.syncYoutubePreview = syncYoutubePreview;

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
 const isEditing = Date.now() < userScrubUntil || document.activeElement === seekTimeInput;
 if (!isEditing && typeof progress.current_time === 'number' && progress.current_time >= 0) {
  const cur = Math.round(progress.current_time);
  updateSeekUI(cur, true);
  if (Math.abs(cur - lastAutoSyncedSec) >= 8) {
   lastAutoSyncedSec = cur;
   seekPreviewFrame(cur);
  }
 } else if (previewDuration > 0) {
  updateSeekUI(Number(seekSlider?.value) || 0, false);
 }
}
window.syncYoutubeProgress = syncYoutubeProgress;

if (previewFrame) {
 previewFrame.addEventListener('load', () => {
  if (!currentPreviewVideoId || !previewFrame.contentWindow) return;
  try {
   previewFrame.contentWindow.postMessage(JSON.stringify({event: 'listening', id: 1, channel: 'widget'}), '*');
   ytCommand('mute');
  } catch {}
 });
}

window.addEventListener('message', event => {
 if (!event.origin.includes('youtube.com') && !event.origin.includes('youtube-nocookie.com')) return;
 try {
  const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
  if (data?.event === 'infoDelivery' && data.info) {
   if (typeof data.info.duration === 'number' && data.info.duration > 0) {
    const dur = Math.round(data.info.duration);
    if (dur !== previewDuration) {
     previewDuration = dur;
     if (seekSlider) seekSlider.max = String(dur);
     updateSeekUI(Number(seekSlider?.value) || 0, false);
    }
   }
   if (!initialPaused && data.info.playerState === 1) {
    initialPaused = true;
    ytCommand('mute');
    ytCommand('pauseVideo');
   }
  }
 } catch {}
});

if (previewToggle && previewWrap) {
 previewToggle.addEventListener('click', () => {
  const hiding = !previewWrap.hidden;
  previewWrap.hidden = hiding;
  previewToggle.setAttribute('aria-expanded', String(!hiding));
  previewToggle.textContent = hiding ? 'Mostra anteprima' : 'Nascondi anteprima';
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
  lastAutoSyncedSec = parsed;
  updateSeekUI(parsed, true);
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
