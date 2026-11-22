"""Comprehensive local screen-sharing evaluation & regression benchmark.

Measures all three layers locally without touching or controlling any TV:
  Suite A: Off-thread encode/decode worker (JPEG desktop/cinema + WebCodecs H.264)
  Suite B: End-to-end browser-to-browser pipeline (/share -> ShareRelay -> /screen)
  Suite C: Python backend microbenchmarks (ShareRelay v2, NativeSession TS/stream, HTTP keep-alive)

Examples:
  python -m tools.benchmark_screen_share --output reports/perf-baseline.json
  python -m tools.benchmark_screen_share --output reports/perf-optimized.json --compare reports/perf-baseline.json
"""
import argparse
import http.client
import io
import json
import math
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

# Metric comparison rules: (path_tuple, label, polarity, rel_tol, abs_tol)
# polarity: 'lower' (lower is better), 'higher' (higher is better)
METRIC_SPECS = [
    (('suite_a', 'desktop_static', 'steady_mean_encode_ms'), 'A/desktop_static encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'desktop_static', 'total_payload_bytes'), 'A/desktop_static bytes', 'lower', 0.02, 512),
    (('suite_a', 'desktop_cursor', 'steady_mean_encode_ms'), 'A/desktop_cursor encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'desktop_cursor', 'total_payload_bytes'), 'A/desktop_cursor bytes', 'lower', 0.03, 1024),
    (('suite_a', 'desktop_cursor', 'mean_ssim'), 'A/desktop_cursor ssim', 'higher', 0.001, 0.001),
    (('suite_a', 'desktop_scroll_mixed', 'steady_mean_encode_ms'), 'A/desktop_scroll encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'desktop_scroll_mixed', 'steady_p95_encode_ms'), 'A/desktop_scroll p95_ms', 'lower', 0.05, 1.0),
    (('suite_a', 'desktop_scroll_mixed', 'total_payload_bytes'), 'A/desktop_scroll bytes', 'lower', 0.03, 2048),
    (('suite_a', 'desktop_scroll_mixed', 'mean_ssim'), 'A/desktop_scroll ssim', 'higher', 0.001, 0.001),
    (('suite_a', 'cinema_motion_balanced', 'steady_mean_encode_ms'), 'A/motion_balanced encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'cinema_motion_balanced', 'psnr_db'), 'A/motion_balanced psnr_db', 'higher', 0.01, 0.05),
    (('suite_a', 'cinema_motion_cinema', 'steady_mean_encode_ms'), 'A/motion_cinema encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'cinema_motion_cinema', 'psnr_db'), 'A/motion_cinema psnr_db', 'higher', 0.01, 0.05),
    (('suite_a', 'h264_720p30_native', 'steady_mean_encode_ms'), 'A/h264_720p30_native encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'h264_720p30_native', 'first_media_packet_frame'), 'A/h264_720p30_native 1st_pkt_frame', 'lower', 0.01, 0.0),
    (('suite_a', 'h264_720p30_native', 'mp4_overhead_bytes'), 'A/h264_720p30_native mp4_overhead', 'lower', 0.02, 64),
    (('suite_a', 'h264_720p30_downscale', 'steady_mean_encode_ms'), 'A/h264_720p30_down encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'h264_720p60_native', 'steady_mean_encode_ms'), 'A/h264_720p60_native encode_ms', 'lower', 0.05, 0.5),
    (('suite_a', 'h264_720p60_native', 'first_media_packet_frame'), 'A/h264_720p60_native 1st_pkt_frame', 'lower', 0.01, 0.0),
    (('suite_b', 'e2e_jpeg_cinema_720p30', 'delivered_fps'), 'B/e2e_jpeg_cinema fps', 'higher', 0.04, 0.5),
    (('suite_b', 'e2e_jpeg_cinema_720p30', 'mean_ack_ms'), 'B/e2e_jpeg_cinema ack_ms', 'lower', 0.05, 1.0),
    (('suite_b', 'e2e_jpeg_cinema_720p30', 'max_frame_gap_ms'), 'B/e2e_jpeg_cinema max_gap_ms', 'lower', 0.10, 5.0),
    (('suite_b', 'e2e_jpeg_desktop_cursor_720p30', 'delivered_fps'), 'B/e2e_jpeg_cursor fps', 'higher', 0.05, 0.5),
    (('suite_b', 'e2e_jpeg_desktop_cursor_720p30', 'mbps'), 'B/e2e_jpeg_cursor mbps', 'lower', 0.05, 0.05),
    (('suite_b', 'e2e_jpeg_desktop_cursor_720p30', 'mean_ack_ms'), 'B/e2e_jpeg_cursor ack_ms', 'lower', 0.05, 1.0),
    (('suite_b', 'e2e_jpeg_slow_tv_pressure', 'delivered_fps'), 'B/e2e_slow_tv fps', 'higher', 0.04, 0.3),
    (('suite_b', 'e2e_jpeg_slow_tv_pressure', 'max_frame_gap_ms'), 'B/e2e_slow_tv max_gap_ms', 'lower', 0.10, 8.0),
    (('suite_b', 'e2e_h264_mse_720p60', 'decoded_fps'), 'B/e2e_h264_60 decoded_fps', 'higher', 0.04, 1.0),
    (('suite_b', 'e2e_h264_mse_720p60', 'mean_buffer_ms'), 'B/e2e_h264_60 buffer_ms', 'lower', 0.05, 5.0),
    (('suite_b', 'e2e_h264_mse_720p60', 'time_to_first_video_ms'), 'B/e2e_h264_60 ttfv_ms', 'lower', 0.08, 25.0),
    (('suite_c', 'relay_throughput_v2', 'packets_per_sec'), 'C/relay packets_per_sec', 'higher', 0.05, 10.0),
    (('suite_c', 'relay_throughput_v2', 'p95_rtt_ms'), 'C/relay p95_rtt_ms', 'lower', 0.05, 0.2),
    (('suite_c', 'native_ts_parser_and_stream', 'ts_parse_mb_s'), 'C/native_ts_parse MB/s', 'higher', 0.05, 2.0),
    (('suite_c', 'native_ts_parser_and_stream', 'stream_read_mb_s'), 'C/native_stream MB/s', 'higher', 0.05, 5.0),
    (('suite_c', 'http_chunk_upload_keepalive', 'requests_per_sec'), 'C/http_chunk req/s', 'higher', 0.05, 10.0),
    (('suite_c', 'http_chunk_upload_keepalive', 'tcp_connections_opened'), 'C/http_chunk tcp_conns', 'lower', 0.01, 0.0),
]


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * pct / 100.0) - 1))
    return ordered[idx]


def _worker_source():
    mp4 = (ROOT / 'web' / 'mp4.js').read_text(encoding='utf-8')
    worker = (ROOT / 'web' / 'share-worker.js').read_text(encoding='utf-8')
    return mp4 + '\n' + worker.replace("importScripts('/assets/mp4.js');", '')


SUITE_A_JS = r"""async ({workerSource, frames}) => {
 function createMetrics(width, height) {
  const luma = (p, i) => 0.2126 * p[i] + 0.7152 * p[i + 1] + 0.0722 * p[i + 2];
  return function metrics(a, b) {
   let error = 0, ssim = 0, windows = 0;
   for (let y = 0; y < height; y += 8) {
    for (let x = 0; x < width; x += 8) {
     let sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0, count = 0;
     const yEnd = Math.min(y + 8, height), xEnd = Math.min(x + 8, width);
     for (let yy = y; yy < yEnd; yy++) {
      for (let xx = x; xx < xEnd; xx++) {
       const i = (yy * width + xx) * 4;
       const va = luma(a, i), vb = luma(b, i);
       sa += va; sb += vb; saa += va * va; sbb += vb * vb; sab += va * vb; count++;
       for (let c = 0; c < 3; c++) error += (a[i + c] - b[i + c]) ** 2;
      }
     }
     const ma = sa / count, mb = sb / count;
     const va = Math.max(0, saa / count - ma * ma), vb = Math.max(0, sbb / count - mb * mb);
     const cov = sab / count - ma * mb;
     ssim += ((2 * ma * mb + 6.5025) * (2 * cov + 58.5225)) / ((ma * ma + mb * mb + 6.5025) * (va + vb + 58.5225));
     windows++;
    }
   }
   const mse = error / (width * height * 3);
   return { mse, psnr_db: mse ? 10 * Math.log10(65025 / mse) : 99.0, ssim: ssim / windows };
  };
 }

 function buildScenes(kind, width, height, count) {
  const src = document.createElement('canvas');
  src.width = width; src.height = height;
  const ctx = src.getContext('2d', { willReadFrequently: true });
  const list = [];
  if (kind === 'static' || kind === 'cursor' || kind === 'scroll_mixed') {
   // Base crisp desktop UI: sidebar, top bar, code/document lines
   const base = document.createElement('canvas');
   base.width = width; base.height = height;
   const bctx = base.getContext('2d');
   bctx.fillStyle = '#18181b'; bctx.fillRect(0, 0, width, height);
   bctx.fillStyle = '#111113'; bctx.fillRect(0, 0, Math.round(width * 0.2), height);
   bctx.fillStyle = '#27272a'; bctx.fillRect(0, 0, width, 44);
   bctx.fillStyle = '#fafafa'; bctx.font = '18px monospace';
   for (let row = 0; row < 18; row++) {
    bctx.fillStyle = row % 3 === 0 ? '#93c5fd' : '#e4e4e7';
    bctx.fillText('const value_' + row + ' = computeScreenDelta(frame, ' + (row * 17) + '); // line ' + row, Math.round(width * 0.24), 84 + row * 32);
   }
   for (let i = 0; i < 8; i++) {
    bctx.fillStyle = '#a1a1aa';
    bctx.fillText('File_' + i + '.js', 24, 90 + i * 36);
   }
   for (let n = 0; n < count; n++) {
    ctx.drawImage(base, 0, 0);
    if (kind === 'cursor') {
     // Small localized change (~2-3% tiles): cursor + badge update
     const cx = 340 + (n % 8) * 14, cy = 170;
     ctx.fillStyle = '#fafafa'; ctx.fillRect(cx, cy, 4, 24);
     ctx.fillStyle = '#27272a'; ctx.fillRect(width - 220, 8, 200, 28);
     ctx.fillStyle = '#4ade80'; ctx.font = '15px sans-serif';
     ctx.fillText('Frame #' + n, width - 200, 27);
    } else if (kind === 'scroll_mixed') {
     // ~30% dirty area: textured gradient card + moving rows in right pane
     const px = Math.round(width * 0.28), py = 80, pw = 640, ph = 384;
     const img = ctx.createImageData(pw, ph), p = img.data;
     for (let y = 0; y < ph; y++) {
      for (let x = 0; x < pw; x++) {
       const idx = (y * pw + x) * 4;
       const noise = ((Math.imul(x + n * 11, 374761393) ^ Math.imul(y + n * 5, 668265263)) >>> 24) / 255;
       p[idx] = Math.min(255, 30 + (170 * x) / pw + noise * 45);
       p[idx + 1] = Math.min(255, 40 + (150 * y) / ph + noise * 45);
       p[idx + 2] = Math.min(255, 70 + 70 * Math.sin((x + n * 13) / 31) + noise * 40);
       p[idx + 3] = 255;
      }
     }
     ctx.putImageData(img, px, py);
     ctx.fillStyle = '#ffffff'; ctx.font = 'bold 26px sans-serif';
     ctx.fillText('Scroll region #' + n, px + 24, py + 48);
    }
    list.push({ canvas: (() => { const c = document.createElement('canvas'); c.width = width; c.height = height; c.getContext('2d').drawImage(src, 0, 0); return c; })(), imageData: ctx.getImageData(0, 0, width, height) });
   }
   return list;
  }
  // Full-motion cinema scene (adapted from benchmark_video_quality.py)
  for (let n = 0; n < count; n++) {
   const pixels = ctx.createImageData(width, height), p = pixels.data;
   const scene = n % 3;
   const light = scene === 1 ? 0.14 : 1;
   const cScale = scene === 2 ? 90 : 20;
   for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
     const i = (y * width + x) * 4;
     const texture = ((Math.imul(x + n * 9, 374761393) ^ Math.imul(y, 668265263)) >>> 24) / 255;
     const wave = 18 * Math.sin((x + n * 15) / 37) + 12 * Math.cos(y / 29);
     p[i] = Math.max(0, Math.min(255, (45 + (140 * x) / width + wave + texture * 28) * light));
     p[i + 1] = Math.max(0, Math.min(255, (30 + (150 * y) / height + wave + texture * 28) * light));
     p[i + 2] = Math.max(0, Math.min(255, (80 + (90 * (x + y)) / (width + height) + texture * cScale) * light));
     p[i + 3] = 255;
    }
   }
   ctx.putImageData(pixels, 0, 0);
   ctx.fillStyle = '#e4c39b'; ctx.beginPath();
   ctx.ellipse(width * (0.25 + (n % 5) * 0.07), height * 0.4, width * 0.07, height * 0.15, 0, 0, Math.PI * 2);
   ctx.fill();
   ctx.fillStyle = '#10151b'; ctx.fillRect(0, height * 0.87, width, height * 0.13);
   ctx.fillStyle = 'white'; ctx.textAlign = 'center';
   ctx.font = Math.round(height * 0.038) + 'px sans-serif';
   ctx.fillText('Dettagli, movimento e sottotitoli — ' + n, width / 2, height * 0.95);
   const clone = document.createElement('canvas'); clone.width = width; clone.height = height;
   clone.getContext('2d').drawImage(src, 0, 0);
   list.push({ canvas: clone, imageData: ctx.getImageData(0, 0, width, height) });
  }
  return list;
 }

 function parseMp4Overhead(bytes) {
  // Sum non-mdat container bytes across ISO BMFF boxes.
  let offset = 0, coded = 0;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  while (offset + 8 <= bytes.byteLength) {
   const size = view.getUint32(offset);
   if (size < 8 || offset + size > bytes.byteLength) break;
   const type = String.fromCharCode(bytes[offset + 4], bytes[offset + 5], bytes[offset + 6], bytes[offset + 7]);
   if (type === 'mdat') coded += size - 8;
   offset += size;
  }
  return { total: bytes.byteLength, coded, overhead: Math.max(0, bytes.byteLength - coded) };
 }

 async function runJpegWorkload(name, sceneKind, profile, width, height, fps, count) {
  const scenes = buildScenes(sceneKind, width, height, count);
  const metrics = createMetrics(width, height);
  const workerURL = URL.createObjectURL(new Blob([workerSource], { type: 'application/javascript' }));
  const worker = new Worker(workerURL);
  const receiver = document.createElement('canvas');
  receiver.width = width; receiver.height = height;
  const out = receiver.getContext('2d', { willReadFrequently: true });
  let waiting = null, packets = [], configured = null;
  worker.onerror = e => { if (waiting) waiting.reject(Error(e.message || 'Worker error')); };
  worker.onmessage = e => {
   const d = e.data;
   if (d.type === 'configure') { configured = { width: d.width, height: d.height }; receiver.width = d.width; receiver.height = d.height; }
   else if (d.type === 'packet') packets.push(d);
   else if (d.type === 'processed' && waiting) { clearTimeout(waiting.timer); waiting.resolve(); waiting = null; }
  };
  worker.postMessage({ type: 'start', codec: 'jpeg', profile, width, fps });
  const samples = [];
  const wallStart = performance.now();
  try {
   for (let n = 0; n < scenes.length; n++) {
    packets = [];
    const start = performance.now();
    await new Promise((resolve, reject) => {
     waiting = { resolve, reject, timer: setTimeout(() => reject(Error('Encoder timeout in ' + name)), 15000) };
     const vf = new VideoFrame(scenes[n].canvas, { timestamp: Math.round((n * 1000000) / fps) });
     worker.postMessage({ type: 'frame', frame: vf }, [vf]);
    });
    const encodeMs = performance.now() - start;
    let bytes = 0;
    for (const packet of packets) {
     bytes += packet.data.byteLength;
     let payload = packet.data, tiles = null;
     if (packet.kind === 5 || packet.kind === 6) {
      const v = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
      const tcount = v.getUint16(0);
      tiles = [];
      for (let i = 0; i < tcount; i++) tiles.push([v.getUint16(2 + i * 4), v.getUint16(4 + i * 4)]);
      payload = new Uint8Array(payload.buffer, payload.byteOffset + 2 + tcount * 4, payload.byteLength - 2 - tcount * 4);
     }
     const png = packet.kind === 6 || packet.kind === 7;
     const image = await createImageBitmap(new Blob([payload], { type: png ? 'image/png' : 'image/jpeg' }));
     if (tiles) {
      const cols = packet.rect[0];
      tiles.forEach(([x, y], i) => {
       const tw = Math.min(64, receiver.width - x), th = Math.min(64, receiver.height - y);
       out.drawImage(image, (i % cols) * 64, Math.floor(i / cols) * 64, tw, th, x, y, tw, th);
      });
     } else {
      out.drawImage(image, ...packet.rect);
     }
     image.close();
    }
    const q = metrics(scenes[n].imageData.data, out.getImageData(0, 0, width, height).data);
    samples.push({ ...q, bytes, encode_ms: encodeMs, packets: packets.length });
   }
  } finally {
   worker.terminate();
   URL.revokeObjectURL(workerURL);
  }
  const totalWallMs = performance.now() - wallStart;
  const mean = k => samples.reduce((s, x) => s + x[k], 0) / samples.length;
  const steady = samples.slice(1).map(s => s.encode_ms).sort((a, b) => a - b);
  return {
   codec: 'jpeg', profile, resolution: configured, frames: samples.length,
   psnr_db: mean('mse') ? 10 * Math.log10(65025 / mean('mse')) : 99.0,
   mean_ssim: mean('ssim'),
   worst_ssim: Math.min(...samples.map(s => s.ssim)),
   mean_encode_ms: mean('encode_ms'),
   steady_mean_encode_ms: steady.length ? steady.reduce((a, b) => a + b, 0) / steady.length : mean('encode_ms'),
   steady_p95_encode_ms: steady.length ? steady[Math.max(0, Math.ceil(steady.length * 0.95) - 1)] : mean('encode_ms'),
   total_payload_bytes: samples.reduce((s, x) => s + x.bytes, 0),
   packets_sent: samples.reduce((s, x) => s + x.packets, 0),
   frames_without_update: samples.filter(s => !s.packets).length,
   total_wall_ms: totalWallMs
  };
 }

 async function runH264Workload(name, srcWidth, srcHeight, targetWidth, fps, count) {
  if (typeof VideoEncoder !== 'function') return { available: false, reason: 'VideoEncoder unavailable' };
  const probeLevel = targetWidth > 1280 ? (fps <= 30 ? '28' : '2A') : (fps <= 30 ? '1F' : '29');
  const probe = await VideoEncoder.isConfigSupported({
   codec: 'avc1.42E0' + probeLevel, width: targetWidth, height: Math.round((srcHeight * targetWidth) / srcWidth / 2) * 2,
   framerate: fps, bitrate: 3000000, latencyMode: 'realtime', avc: { format: 'avc' }
  });
  if (!probe.supported) return { available: false, reason: 'AVC VideoEncoder unsupported' };

  const scenes = buildScenes('motion', srcWidth, srcHeight, count);
  const workerURL = URL.createObjectURL(new Blob([workerSource], { type: 'application/javascript' }));
  const worker = new Worker(workerURL);
  let waiting = null, packets = [], allPackets = [], configured = null, modeFallback = false, firstMediaFrame = null;
  let currentFrameIndex = 0;
  worker.onerror = e => { if (waiting) waiting.reject(Error(e.message || 'Worker error')); };
  worker.onmessage = e => {
   const d = e.data;
   if (d.type === 'configure') configured = { width: d.width, height: d.height, mime: d.mime };
   else if (d.type === 'mode' && d.mode === 'jpeg') modeFallback = true;
   else if (d.type === 'packet') {
    packets.push(d); allPackets.push(d);
    if (d.kind === 2 && firstMediaFrame === null) firstMediaFrame = currentFrameIndex;
   }
   else if (d.type === 'processed' && waiting) { clearTimeout(waiting.timer); waiting.resolve(); waiting = null; }
  };
  worker.postMessage({ type: 'start', codec: 'h264', profile: 'cinema', width: targetWidth, fps });
  const encodeTimes = [];
  const wallStart = performance.now();
  try {
   for (let n = 0; n < scenes.length; n++) {
    currentFrameIndex = n;
    packets = [];
    const start = performance.now();
    await new Promise((resolve, reject) => {
     waiting = { resolve, reject, timer: setTimeout(() => reject(Error('H264 timeout in ' + name + ' at frame ' + n)), 15000) };
     const vf = new VideoFrame(scenes[n].canvas, { timestamp: Math.round((n * 1000000) / fps), duration: Math.round(1000000 / fps) });
     worker.postMessage({ type: 'frame', frame: vf }, [vf]);
    });
    encodeTimes.push(performance.now() - start);
    if (modeFallback) break;
   }
   // Allow async VideoEncoder output callbacks a brief settle window to flush queued chunks.
   await new Promise(r => setTimeout(r, 120));
  } finally {
   worker.terminate();
   URL.revokeObjectURL(workerURL);
  }
  const totalWallMs = performance.now() - wallStart;
  if (modeFallback) return { available: false, reason: 'Worker fell back to JPEG' };
  let totalBytes = 0, codedBytes = 0, overheadBytes = 0, mediaFrames = 0;
  for (const p of allPackets) {
   const info = parseMp4Overhead(p.data);
   totalBytes += info.total; codedBytes += info.coded; overheadBytes += info.overhead;
   if (p.kind === 2) mediaFrames += (p.count || 1);
  }
  const steady = encodeTimes.slice(1).sort((a, b) => a - b);
  const meanAll = encodeTimes.reduce((a, b) => a + b, 0) / encodeTimes.length;
  return {
   available: true, codec: 'h264', resolution: configured, frames: scenes.length,
   mean_encode_ms: meanAll,
   steady_mean_encode_ms: steady.length ? steady.reduce((a, b) => a + b, 0) / steady.length : meanAll,
   steady_p95_encode_ms: steady.length ? steady[Math.max(0, Math.ceil(steady.length * 0.95) - 1)] : meanAll,
   first_media_packet_frame: firstMediaFrame === null ? count : firstMediaFrame,
   packets_sent: allPackets.length,
   media_frames_emitted: mediaFrames,
   skipped_frames: Math.max(0, count - mediaFrames),
   total_payload_bytes: totalBytes,
   mp4_coded_bytes: codedBytes,
   mp4_overhead_bytes: overheadBytes,
   total_wall_ms: totalWallMs
  };
 }

 return {
  desktop_static: await runJpegWorkload('desktop_static', 'static', 'balanced', 1280, 720, 30, frames),
  desktop_cursor: await runJpegWorkload('desktop_cursor', 'cursor', 'balanced', 1280, 720, 30, frames),
  desktop_scroll_mixed: await runJpegWorkload('desktop_scroll_mixed', 'scroll_mixed', 'balanced', 1280, 720, 30, frames),
  cinema_motion_balanced: await runJpegWorkload('cinema_motion_balanced', 'motion', 'balanced', 1280, 720, 30, frames),
  cinema_motion_cinema: await runJpegWorkload('cinema_motion_cinema', 'motion', 'cinema', 1280, 720, 30, frames),
  h264_720p30_native: await runH264Workload('h264_720p30_native', 1280, 720, 1280, 30, frames),
  h264_720p30_downscale: await runH264Workload('h264_720p30_downscale', 1920, 1080, 1280, 30, frames),
  h264_720p60_native: await runH264Workload('h264_720p60_native', 1280, 720, 1280, 60, frames),
 };
}"""


def run_suite_a(frames=24):
    from playwright.sync_api import sync_playwright
    worker_source = _worker_source()
    with tempfile.TemporaryDirectory(prefix='smart-tv-bench-a-') as tmpdir:
        html = Path(tmpdir) / 'bench.html'
        html.write_text('<!doctype html><title>Suite A</title>', encoding='utf-8')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page()
                page.goto(html.as_uri())
                result = page.evaluate(SUITE_A_JS, {'workerSource': worker_source, 'frames': frames})
                return result
            finally:
                browser.close()


RECEIVER_OBSERVER_JS = r"""delayMs => {
 window.__bench = {
  rxPackets: 0, rxBytes: 0, ackedDecoded: 0, ackedSkipped: 0,
  ackIntervals: [], lastRenderedAt: null, decodeTimes: [], bufferSamples: [],
  fallbacks: 0, firstVideoAt: null, firstVideoEpoch: null, closed: false, startedAt: performance.now()
 };
 document.addEventListener('DOMContentLoaded', () => {
  const v = document.getElementById('screen-video');
  if (v) {
   const mark = () => {
    if (window.__bench.firstVideoEpoch === null && v.videoWidth > 0) {
     window.__bench.firstVideoEpoch = Date.now();
    }
   };
   v.addEventListener('loadeddata', mark);
   v.addEventListener('playing', mark);
   v.addEventListener('timeupdate', mark);
  }
 });
 if (delayMs > 0) {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  if (typeof window.createImageBitmap === 'function') {
   const origCIB = window.createImageBitmap.bind(window);
   window.createImageBitmap = async (...args) => { await sleep(delayMs); return origCIB(...args); };
  }
  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  if (desc && desc.set) {
   Object.defineProperty(HTMLImageElement.prototype, 'src', {
    configurable: true, enumerable: desc.enumerable,
    get: desc.get,
    set(val) {
     if (typeof val === 'string' && val.startsWith('blob:')) {
      setTimeout(() => desc.set.call(this, val), delayMs);
     } else {
      desc.set.call(this, val);
     }
    }
   });
  }
 }
 const OrigWS = window.WebSocket;
 function WrappedWS(url, protocols) {
  const ws = protocols !== undefined ? new OrigWS(url, protocols) : new OrigWS(url);
  const origSend = ws.send.bind(ws);
  ws.send = data => {
   if (typeof data === 'string') {
    try {
     const msg = JSON.parse(data);
     if (msg.type === 'ack') {
      if (typeof msg.decodeMs === 'number') {
       const now = performance.now();
       window.__bench.ackedDecoded++;
       window.__bench.decodeTimes.push(msg.decodeMs);
       if (window.__bench.lastRenderedAt !== null) {
        window.__bench.ackIntervals.push(now - window.__bench.lastRenderedAt);
       }
       window.__bench.lastRenderedAt = now;
       if (window.__bench.firstVideoAt === null) window.__bench.firstVideoAt = now;
       if (window.__bench.firstVideoEpoch === null) window.__bench.firstVideoEpoch = Date.now();
      } else {
       window.__bench.ackedSkipped++;
      }
      if (typeof msg.bufferMs === 'number') window.__bench.bufferSamples.push(msg.bufferMs);
     } else if (msg.type === 'fallback') {
      window.__bench.fallbacks++;
     }
    } catch (_) {}
   }
   return origSend(data);
  };
  ws.addEventListener('message', ev => {
   if (typeof ev.data !== 'string') {
    window.__bench.rxPackets++;
    window.__bench.rxBytes += ev.data.byteLength || ev.data.size || 0;
   }
  });
  ws.addEventListener('close', () => { window.__bench.closed = true; });
  return ws;
 }
 for (const k of ['OPEN', 'CLOSED', 'CONNECTING', 'CLOSING']) WrappedWS[k] = OrigWS[k];
 WrappedWS.prototype = OrigWS.prototype;
 window.WebSocket = WrappedWS;
}"""


SENDER_OBSERVER_JS = r"""({scene, width, height, fps}) => {
 window.__senderBench = { sentPackets: 0, sentBytes: 0, acks: 0, ackRtts: [], codec: 'unknown', clickedAt: 0 };
 const OrigWS = window.WebSocket;
 function WrappedWS(url, protocols) {
  const ws = protocols !== undefined ? new OrigWS(url, protocols) : new OrigWS(url);
  const origSend = ws.send.bind(ws);
  ws.send = data => {
   if (typeof data !== 'string') {
    window.__senderBench.sentPackets++;
    window.__senderBench.sentBytes += data.byteLength || data.size || 0;
   } else {
    try {
     const j = JSON.parse(data);
     if (j.type === 'configure') window.__senderBench.codec = j.codec;
    } catch (_) {}
   }
   return origSend(data);
  };
  ws.addEventListener('message', ev => {
   if (typeof ev.data === 'string') {
    try {
     const j = JSON.parse(ev.data);
     if (j.type === 'ack') {
      window.__senderBench.acks++;
      if (typeof j.ackMs === 'number') window.__senderBench.ackRtts.push(j.ackMs);
     }
    } catch (_) {}
   }
  });
  return ws;
 }
 for (const k of ['OPEN', 'CLOSED', 'CONNECTING', 'CLOSING']) WrappedWS[k] = OrigWS[k];
 WrappedWS.prototype = OrigWS.prototype;
 window.WebSocket = WrappedWS;

 navigator.mediaDevices.getDisplayMedia = async () => {
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d');
  let n = 0;
  function draw() {
   n++;
   if (scene === 'cursor') {
    ctx.fillStyle = '#141820'; ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#e4e4e7'; ctx.font = '24px monospace';
    for (let r = 0; r < 14; r++) ctx.fillText('Desktop line #' + r + ' static content stable', 48, 80 + r * 40);
    ctx.fillStyle = '#4ade80'; ctx.fillRect(360 + (n % 12) * 14, 180, 12, 28);
   } else {
    ctx.fillStyle = '#0f172a'; ctx.fillRect(0, 0, width, height);
    const grad = ctx.createLinearGradient(0, 0, width, height);
    grad.addColorStop(0, '#1e3a8a'); grad.addColorStop(0.5, '#7c3aed'); grad.addColorStop(1, '#be185d');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, width, height);
    for (let i = 0; i < 8; i++) {
     ctx.fillStyle = i % 2 ? 'rgba(250,250,250,0.85)' : 'rgba(56,189,248,0.85)';
     ctx.fillRect((n * 17 + i * 143) % (width - 140), 70 + i * 74, 130, 44);
    }
    ctx.fillStyle = '#111827'; ctx.fillRect(0, height - 88, width, 88);
    ctx.fillStyle = '#ffffff'; ctx.font = 'bold 30px sans-serif';
    ctx.fillText('Live stream benchmark frame ' + n, 40, height - 34);
   }
  }
  draw();
  const period = 1000 / fps;
  let next = performance.now() + period;
  let alive = true;
  function tick() {
   if (!alive) return;
   draw();
   next += period;
   const delay = Math.max(1, next - performance.now());
   if (delay < 0) next = performance.now() + period;
   setTimeout(tick, delay);
  }
  setTimeout(tick, period);
  const stream = canvas.captureStream(fps);
  const track = stream.getVideoTracks()[0];
  const origStop = track.stop.bind(track);
  track.stop = () => { alive = false; origStop(); };
  return stream;
 };
}"""


def _run_e2e_scenario(browser, base_url, relay, scenario):
    from playwright.sync_api import expect
    rx_ctx = browser.new_context(viewport={'width': 1280, 'height': 720})
    tx_ctx = browser.new_context(viewport={'width': 1280, 'height': 800})
    rx_errors, tx_errors = [], []
    rx = rx_ctx.new_page()
    tx = tx_ctx.new_page()
    rx.on('pageerror', lambda e: rx_errors.append(str(e)))
    tx.on('pageerror', lambda e: tx_errors.append(str(e)))
    try:
        rx.add_init_script(f"({RECEIVER_OBSERVER_JS})({scenario['delay_ms']});")
        rx.goto(base_url + '/screen')
        expect(rx.locator('#status')).to_contain_text('TV collegata', timeout=8000)

        tx.add_init_script(f"({SENDER_OBSERVER_JS})({json.dumps(scenario['source'])});")
        tx.goto(base_url + '/share')
        for sect in ('#video-section', '#audio-section'):
            loc = tx.locator(sect)
            if loc.count() and loc.get_attribute('open') is None:
                loc.locator('summary').first.click()
        tx.locator('#audio-enabled').uncheck()
        tx.locator('#video-profile').select_option(scenario['profile'])
        tx.locator('#width').select_option(str(scenario['width']))
        tx.locator('#fps').select_option(str(scenario['fps']))
        tx.locator('#transport').select_option(scenario['transport'])

        click_epoch_ms = tx.evaluate("() => Date.now()")
        t0 = time.monotonic()
        tx.locator('#start').click()
        expect(tx.locator('#status')).to_contain_text('ricevute', timeout=12000)

        if scenario['transport'] == 'auto':
            # Wait for video element to actually start playing first frame
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                vstats = rx.evaluate("""() => {
                    const v = document.getElementById('screen-video');
                    const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
                    return { width: v.videoWidth, time: v.currentTime, total: q ? q.totalVideoFrames : 0 };
                }""")
                if vstats['width'] > 0 and (vstats['time'] > 0 or vstats['total'] > 0):
                    break
                time.sleep(0.05)
        fallback_ttfv = (time.monotonic() - t0) * 1000.0

        # Warmup
        time.sleep(scenario['warmup_s'])

        rx_start = rx.evaluate("""() => {
            const v = document.getElementById('screen-video');
            const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
            return {
                ...window.__bench,
                vTotal: q ? q.totalVideoFrames : 0,
                vDropped: q ? q.droppedVideoFrames : 0,
                ackLen: window.__bench.ackIntervals.length,
                bufLen: window.__bench.bufferSamples.length,
                decLen: window.__bench.decodeTimes.length
            };
        }""")
        tx_start = tx.evaluate("() => ({ ...window.__senderBench, rttLen: window.__senderBench.ackRtts.length })")
        measure_start = time.monotonic()
        time.sleep(scenario['duration_s'])
        elapsed = time.monotonic() - measure_start

        rx_end = rx.evaluate("""() => {
            const v = document.getElementById('screen-video');
            const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
            return {
                ...window.__bench,
                vTotal: q ? q.totalVideoFrames : 0,
                vDropped: q ? q.droppedVideoFrames : 0
            };
        }""")
        tx_end = tx.evaluate("() => window.__senderBench")

        tx.locator('#stop').click()
        time.sleep(0.4)

        intervals = rx_end['ackIntervals'][rx_start['ackLen']:]
        buffers = rx_end['bufferSamples'][rx_start['bufLen']:]
        decodes = rx_end['decodeTimes'][rx_start['decLen']:]
        rtts = tx_end['ackRtts'][tx_start['rttLen']:]
        decoded_delta = rx_end['ackedDecoded'] - rx_start['ackedDecoded']
        skipped_delta = rx_end['ackedSkipped'] - rx_start['ackedSkipped']
        bytes_delta = tx_end['sentBytes'] - tx_start['sentBytes']

        first_epoch = rx_end.get('firstVideoEpoch')
        time_to_first = max(0.0, first_epoch - click_epoch_ms) if first_epoch else fallback_ttfv

        result = {
            'codec': tx_end['codec'],
            'elapsed_s': round(elapsed, 3),
            'delivered_fps': round(decoded_delta / elapsed, 2),
            'skipped_stale_frames': skipped_delta,
            'frame_interval_p50_ms': round(_percentile(intervals, 50), 2) if intervals else None,
            'frame_interval_p95_ms': round(_percentile(intervals, 95), 2) if intervals else None,
            'max_frame_gap_ms': round(max(intervals), 2) if intervals else None,
            'mean_ack_ms': round(sum(rtts) / len(rtts), 2) if rtts else None,
            'p95_ack_ms': round(_percentile(rtts, 95), 2) if rtts else None,
            'mean_decode_ms': round(sum(decodes) / len(decodes), 2) if decodes else None,
            'mean_buffer_ms': round(sum(buffers) / len(buffers), 2) if buffers else None,
            'p95_buffer_ms': round(_percentile(buffers, 95), 2) if buffers else None,
            'mbps': round(bytes_delta * 8.0 / elapsed / 1e6, 3),
            'time_to_first_video_ms': round(time_to_first, 1),
            'disconnected': bool(rx_end['closed']),
            'fallbacks': rx_end['fallbacks'],
            'errors': rx_errors + tx_errors,
        }
        if scenario['transport'] == 'auto':
            v_tot = rx_end['vTotal'] - rx_start['vTotal']
            v_drop = rx_end['vDropped'] - rx_start['vDropped']
            result['decoded_fps'] = round(max(0, v_tot - v_drop) / elapsed, 2)
            result['dropped_frames'] = v_drop
        return result
    finally:
        rx_ctx.close()
        tx_ctx.close()


def run_suite_b():
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix='smart-tv-bench-b-') as runtime, \
            patch.dict(os.environ, {'LOCALAPPDATA': runtime, 'TV_HOST': '127.0.0.1'}):
        import server
        from view_state import ViewState
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.daemon_threads = True
        relay = server.ShareRelay(server.CSRF, http_port=http.server_port)
        relay.start('127.0.0.1', 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        remote = Mock()
        remote.tv.is_alive.return_value = True
        remote.send.return_value = {'status': 'sent'}
        remote.close_screen.return_value = {'status': 'sent'}
        remote.open_screen.return_value = {'status': 'sent'}

        scenarios = {
            'e2e_jpeg_cinema_720p30': {
                'profile': 'cinema', 'width': 1280, 'fps': 30, 'transport': 'jpeg',
                'delay_ms': 0, 'warmup_s': 1.5, 'duration_s': 4.0,
                'source': {'scene': 'motion', 'width': 1280, 'height': 720, 'fps': 30},
            },
            'e2e_jpeg_desktop_cursor_720p30': {
                'profile': 'balanced', 'width': 1280, 'fps': 30, 'transport': 'jpeg',
                'delay_ms': 0, 'warmup_s': 1.5, 'duration_s': 4.0,
                'source': {'scene': 'cursor', 'width': 1280, 'height': 720, 'fps': 30},
            },
            'e2e_jpeg_slow_tv_pressure': {
                'profile': 'cinema', 'width': 1280, 'fps': 30, 'transport': 'jpeg',
                'delay_ms': 40, 'warmup_s': 1.5, 'duration_s': 4.0,
                'source': {'scene': 'motion', 'width': 1280, 'height': 720, 'fps': 30},
            },
            'e2e_h264_mse_720p60': {
                'profile': 'cinema', 'width': 1280, 'fps': 60, 'transport': 'auto',
                'delay_ms': 0, 'warmup_s': 1.5, 'duration_s': 4.0,
                'source': {'scene': 'motion', 'width': 1280, 'height': 720, 'fps': 60},
            },
        }
        results = {}
        try:
            with patch.object(server, 'sharing', relay), patch.object(server, 'remote', remote), \
                    patch.object(server, 'view', ViewState(Path(runtime) / 'view.json')), \
                    sync_playwright() as pw:
                browser = pw.chromium.launch(
                    channel='msedge', headless=True,
                    args=['--autoplay-policy=no-user-gesture-required']
                )
                try:
                    for name, cfg in scenarios.items():
                        # Ensure no leftover session state between scenarios
                        try:
                            relay.end_session()
                        except Exception:
                            pass
                        time.sleep(0.1)
                        results[name] = _run_e2e_scenario(browser, base, relay, cfg)
                finally:
                    browser.close()
        finally:
            relay.stop()
            http.shutdown()
            http.server_close()
            thread.join()
        return results


def _bench_relay_v2():
    from websockets.sync.client import connect
    from screen_share import ShareRelay

    relay = ShareRelay('bench-key', ack_timeout=5)
    relay.start('127.0.0.1', 0)
    url = f'ws://127.0.0.1:{relay.port}/screen'
    jpeg_body = b'\xff\xd8' + b'A' * (120 * 1024) + b'\xff\xd9'
    tiles = [(x, y) for y in range(0, 720, 64) for x in range(0, 1280, 64)][:180]
    atlas_body = (
        struct.pack('!H', len(tiles))
        + b''.join(struct.pack('!HH', tx, ty) for tx, ty in tiles)
        + b'\xff\xd8' + b'B' * (48 * 1024) + b'\xff\xd9'
    )

    try:
        viewer = connect(url, origin='http://127.0.0.1:8765', proxy=None, compression=None)
        sender = connect(url, origin='http://127.0.0.1:8765', proxy=None, compression=None)
        viewer.send(json.dumps({'key': 'bench-key', 'role': 'viewer', 'protocol': 2, 'capabilities': {'avc': False}}))
        viewer.recv(timeout=2)
        sender.send(json.dumps({'key': 'bench-key', 'role': 'sender', 'protocol': 2}))
        ready = json.loads(sender.recv(timeout=2))
        window = ready['window']

        sender.send(json.dumps({'type': 'configure', 'codec': 'jpeg', 'width': 1280, 'height': 720}))
        viewer.recv(timeout=2)

        stop_flag = threading.Event()

        def receiver_loop():
            while not stop_flag.is_set():
                try:
                    msg = viewer.recv(timeout=1)
                except TimeoutError:
                    continue
                except Exception:
                    break
                if isinstance(msg, bytes) and len(msg) >= 32:
                    seq = struct.unpack_from('!I', msg, 8)[0]
                    viewer.send(json.dumps({'type': 'ack', 'seq': seq, 'bufferMs': 0, 'decodeMs': 1.0}))

        rx_thread = threading.Thread(target=receiver_loop, daemon=True)
        rx_thread.start()

        total_packets = 200
        sent_times = {}
        rtts = []
        total_bytes = 0
        seq = 0
        inflight = 0
        t_start = time.perf_counter()

        while seq < total_packets or inflight > 0:
            while inflight < window and seq < total_packets:
                seq += 1
                if seq % 2 == 1:
                    pkt = struct.pack('!4sBBHI6HII', b'SV2\0', 4, 1, 1, seq, 1280, 720, 0, 0, 1280, 720, 0, 0) + jpeg_body
                else:
                    pkt = struct.pack('!4sBBHI6HII', b'SV2\0', 5, 0, 1, seq, 1280, 720, 8, 64, 512, 1472, 0, 0) + atlas_body
                sent_times[seq] = time.perf_counter()
                total_bytes += len(pkt)
                sender.send(pkt)
                inflight += 1
            msg = json.loads(sender.recv(timeout=5))
            if msg.get('type') == 'ack':
                ack_seq = msg['seq']
                rtts.append((time.perf_counter() - sent_times.pop(ack_seq)) * 1000.0)
                inflight -= 1

        elapsed = time.perf_counter() - t_start
        stop_flag.set()
        viewer.close()
        sender.close()
        rx_thread.join(timeout=2)
        return {
            'packets': total_packets,
            'total_mb': round(total_bytes / (1024 * 1024), 2),
            'elapsed_ms': round(elapsed * 1000.0, 2),
            'packets_per_sec': round(total_packets / elapsed, 1),
            'throughput_mb_s': round((total_bytes / (1024 * 1024)) / elapsed, 2),
            'mean_rtt_ms': round(sum(rtts) / len(rtts), 3),
            'p95_rtt_ms': round(_percentile(rtts, 95), 3),
        }
    finally:
        relay.stop()


def _bench_native_ts_and_stream():
    from native_video import NativeSession

    def make_packet(pts, stream_id):
        stamp = bytes([
            0x21 | ((pts >> 29) & 14),
            (pts >> 22) & 255,
            ((pts >> 14) & 254) | 1,
            (pts >> 7) & 255,
            ((pts << 1) & 254) | 1,
        ])
        pes = b'\x00\x00\x01' + bytes([stream_id]) + b'\x00\x00\x80\x80\x05' + stamp
        hdr = b'\x47\x41\x00\x10' + pes
        return hdr + b'\xff' * (188 - len(hdr))

    # Build ~1 MiB of interleaved video/audio TS packets
    one_mb_packets = []
    for n in range(2800):
        one_mb_packets.append(make_packet(n * 1500, 0xE0))
        one_mb_packets.append(make_packet(n * 1500, 0xC0))
    ts_block = b''.join(one_mb_packets)

    proc = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
    with patch('native_video.subprocess.Popen', return_value=proc), patch('native_video.threading.Thread'):
        sess = NativeSession(Mock(), 'http://192.0.2.1:8765')

    repeats = 32
    total_ts_bytes = len(ts_block) * repeats
    t0 = time.perf_counter()
    for _ in range(repeats):
        for off in range(0, len(ts_block), 4096):
            sess._measure_video(ts_block[off:off + 4096])
    ts_elapsed = time.perf_counter() - t0
    sess.close()

    # Stream read throughput across 512 x 32 KiB chunks (16 MiB)
    proc2 = Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
    with patch('native_video.subprocess.Popen', return_value=proc2), patch('native_video.threading.Thread'):
        sess2 = NativeSession(Mock(), 'http://192.0.2.1:8765')
    chunk = b'X' * 32768
    num_chunks = 512
    for idx in range(num_chunks):
        sess2.chunks.append((idx * len(chunk), chunk))
    sess2.end = num_chunks * len(chunk)
    gen = sess2.stream()
    read_bytes = 0
    t1 = time.perf_counter()
    for _ in range(num_chunks):
        read_bytes += len(next(gen))
    stream_elapsed = time.perf_counter() - t1
    sess2.close()

    return {
        'ts_mb': round(total_ts_bytes / (1024 * 1024), 2),
        'ts_parse_ms': round(ts_elapsed * 1000.0, 2),
        'ts_parse_mb_s': round((total_ts_bytes / (1024 * 1024)) / ts_elapsed, 2),
        'stream_mb': round(read_bytes / (1024 * 1024), 2),
        'stream_read_ms': round(stream_elapsed * 1000.0, 2),
        'stream_read_mb_s': round((read_bytes / (1024 * 1024)) / stream_elapsed, 2),
    }


def _bench_http_keepalive():
    with tempfile.TemporaryDirectory(prefix='smart-tv-bench-c-') as runtime, \
            patch.dict(os.environ, {'LOCALAPPDATA': runtime, 'TV_HOST': '127.0.0.1'}):
        import server
        http_srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http_srv.daemon_threads = True
        thread = threading.Thread(target=http_srv.serve_forever, daemon=True)
        thread.start()

        class CountingHTTPConnection(http.client.HTTPConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.connect_count = 0

            def connect(self):
                self.connect_count += 1
                super().connect()

        fake_session = Mock()
        fake_session.feed.return_value = None
        payload = b'\x00' * (64 * 1024)
        latencies = []
        n_reqs = 60
        try:
            with patch.object(server.native_cast, 'get', return_value=fake_session):
                conn = CountingHTTPConnection('127.0.0.1', http_srv.server_port, timeout=5)
                origin = f'http://127.0.0.1:{http_srv.server_port}'
                headers = {
                    'Host': f'127.0.0.1:{http_srv.server_port}',
                    'Origin': origin,
                    'X-Remote-Key': server.CSRF,
                    'Content-Type': 'video/mp4',
                }
                t0 = time.perf_counter()
                for _ in range(n_reqs):
                    req_t0 = time.perf_counter()
                    conn.request('POST', '/api/native/sess123/chunk', body=payload, headers=headers)
                    resp = conn.getresponse()
                    resp.read()
                    if resp.status != 200:
                        raise RuntimeError(f'Unexpected chunk POST status: {resp.status}')
                    latencies.append((time.perf_counter() - req_t0) * 1000.0)
                total_elapsed = time.perf_counter() - t0
                conns_opened = conn.connect_count
                conn.close()
        finally:
            http_srv.shutdown()
            http_srv.server_close()
            thread.join()

        return {
            'requests': n_reqs,
            'payload_kb': 64,
            'elapsed_ms': round(total_elapsed * 1000.0, 2),
            'requests_per_sec': round(n_reqs / total_elapsed, 1),
            'p95_post_ms': round(_percentile(latencies, 95), 3),
            'tcp_connections_opened': conns_opened,
        }


def run_suite_c():
    return {
        'relay_throughput_v2': _bench_relay_v2(),
        'native_ts_parser_and_stream': _bench_native_ts_and_stream(),
        'http_chunk_upload_keepalive': _bench_http_keepalive(),
    }


def _extract(doc, path):
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None


def compare_reports(baseline, current):
    rows = []
    counts = {'improved': 0, 'regressed': 0, 'stable': 0}
    for path, label, polarity, rel_tol, abs_tol in METRIC_SPECS:
        b = _extract(baseline, path)
        c = _extract(current, path)
        if b is None or c is None:
            continue
        diff = c - b
        tol = max(abs_tol, abs(b) * rel_tol)
        if abs(diff) <= tol:
            verdict = 'STABLE'
            counts['stable'] += 1
        elif (polarity == 'lower' and diff < 0) or (polarity == 'higher' and diff > 0):
            verdict = 'IMPROVED'
            counts['improved'] += 1
        else:
            verdict = 'REGRESSED'
            counts['regressed'] += 1
        pct = (diff / b * 100.0) if b else 0.0
        rows.append({
            'metric': label, 'baseline': b, 'current': c,
            'delta': diff, 'pct': pct, 'verdict': verdict,
        })

    print('\n| Metric | Baseline | Current | Delta | Verdict |')
    print('|---|---:|---:|---:|---|')
    for r in rows:
        print(f"| {r['metric']} | {r['baseline']:.3g} | {r['current']:.3g} | {r['delta']:+.3g} ({r['pct']:+.1f}%) | {r['verdict']} |")
    print(f"\nSummary: {counts['improved']} improved, {counts['regressed']} regressed, {counts['stable']} stable")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=int, default=24)
    parser.add_argument('--suites', default='A,B,C', help='Comma-separated subset of A,B,C')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--fail-on-regression', action='store_true')
    args = parser.parse_args()

    selected = {s.strip().upper() for s in args.suites.split(',') if s.strip()}
    report = {'scope': 'Local screen-sharing benchmark; no real TV contacted'}
    if 'A' in selected:
        report['suite_a'] = run_suite_a(frames=args.frames)
    if 'B' in selected:
        report['suite_b'] = run_suite_b()
    if 'C' in selected:
        report['suite_c'] = run_suite_c()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding='utf-8'))
        counts = compare_reports(baseline, report)
        if args.fail_on_regression and counts['regressed'] > 0:
            sys.exit(1)


if __name__ == '__main__':
    main()
