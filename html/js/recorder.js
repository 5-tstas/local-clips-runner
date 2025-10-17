// ./js/recorder.js
// Универсальный рекордер WebM (VP9→VP8) через canvas.captureStream + MediaRecorder.
// Требования: 1280x720@30fps, ~5 Mbps, корректная duration.
// Ожидается глобальная функция window.webmDurationFix(blob, durationMs) из html/vendor/webm-duration-fix.min.js.
// Если фиксер отсутствует/падает — используем raw-blob (лучше, чем null-duration).

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function pickWebmMime() {
  const candidates = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm'
  ];
  const sup = (t) => window.MediaRecorder?.isTypeSupported?.(t);
  return candidates.find((t) => sup?.(t)) || 'video/webm';
}

function collectTracks(extra) {
  const collected = [];
  const push = (track) => {
    if (!track) return;
    if (typeof MediaStreamTrack !== 'undefined' && track instanceof MediaStreamTrack) {
      collected.push(track);
      return;
    }
    if (track.getTracks) {
      for (const t of track.getTracks()) push(t);
    }
  };
  if (extra == null) return collected;
  const arr = Array.isArray(extra) ? extra : [extra];
  for (const item of arr) push(item);
  return collected;
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000, extraTracks = [] } = {}) {
  if (!(canvas instanceof HTMLCanvasElement)) {
    throw new TypeError('startRecorder: требуется canvas');
  }
  const baseStream = canvas.captureStream?.(fps);
  if (!baseStream) {
    throw new Error('canvas.captureStream() не поддерживается');
  }

  const mimeType = pickWebmMime();
  const stream = new MediaStream();
  for (const track of baseStream.getTracks()) stream.addTrack(track);
  for (const track of collectTracks(extraTracks)) stream.addTrack(track);

  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });
  const chunks = [];
  let startedAt = 0;

  rec.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) chunks.push(e.data);
  });
  rec.addEventListener('start', () => {
    startedAt = performance.now();
  });
  rec.addEventListener('error', (e) => {
    console.error('MediaRecorder error', e);
  });

  return { rec, chunks, mimeType, getStartedMs: () => startedAt };
}

async function fixDuration(blob, durationMs) {
  if (typeof window.webmDurationFix === 'function') {
    try {
      const fixed = await window.webmDurationFix(blob, durationMs);
      if (fixed) return fixed;
    } catch (err) {
      console.warn('webmDurationFix failed, fallback to raw blob', err);
    }
  }
  return blob;
}

export async function stopAndDownload(recCtx, outName = 'export.webm', { finalDelayMs = 300 } = {}) {
  if (!recCtx || !recCtx.rec) {
    throw new Error('stopAndDownload: некорректный recorder context');
  }
  const { rec, chunks, mimeType, getStartedMs } = recCtx;
  if (finalDelayMs > 0) {
    await sleep(finalDelayMs);
  }

  const done = new Promise((resolve) => {
    rec.addEventListener('stop', async () => {
      const recordedMs = Math.max(0, performance.now() - getStartedMs());
      const raw = new Blob(chunks, { type: mimeType });
      const blob = await fixDuration(raw, recordedMs);
      downloadBlob(blob, outName);
      resolve(blob);
    }, { once: true });
  });

  if (rec.state !== 'inactive') {
    try { rec.stop(); } catch (err) { console.error('MediaRecorder stop failed', err); }
  }
  return done;
}

export function downloadBlob(blob, filename = 'export.webm') {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function bindGlobalExport(recCtx, outName = 'export.webm') {
  window.exportWebM = async () => {
    await stopAndDownload(recCtx, outName, { finalDelayMs: 300 });
  };
  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn) {
    btn.addEventListener('click', () => window.exportWebM());
  }
}
