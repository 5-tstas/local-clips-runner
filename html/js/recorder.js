// ./js/recorder.js
// Универсальный рекордер WebM (VP9→VP8) через canvas.captureStream + MediaRecorder.
// Требования: 1280x720@30fps, ~5 Mbps, корректная duration.
// Ожидается глобальная функция window.webmDurationFix(blob, durationMs) из html/vendor/webm-duration-fix.min.js.
// Если фиксер отсутствует/падает — используем raw-blob (лучше, чем null-duration).

const GLOBAL_LOG_KEY = '__RECORDER_LOG__';
const GLOBAL_T0_KEY = '__RECORDER_T0__';

function ensureLog() {
  const win = window;
  if (!Array.isArray(win[GLOBAL_LOG_KEY])) {
    win[GLOBAL_LOG_KEY] = [];
    win[GLOBAL_T0_KEY] = undefined;
  }
  return win[GLOBAL_LOG_KEY];
}

function nowMs() {
  return performance.now();
}

export function markStage(name, detail) {
  const log = ensureLog();
  const rawNow = nowMs();
  if (typeof window[GLOBAL_T0_KEY] !== 'number') {
    window[GLOBAL_T0_KEY] = rawNow;
  }
  const elapsed = rawNow - (window[GLOBAL_T0_KEY] ?? rawNow);
  const entry = {
    name,
    elapsed_ms: Number(elapsed.toFixed(2)),
    perf_now: Number(rawNow.toFixed(2)),
    wall_time: new Date().toISOString(),
    detail: detail ?? null,
  };
  log.push(entry);
  try {
    // Упрощённый лог для ручной диагностики
    const payload = detail ? JSON.stringify(detail) : '';
    console.info(`[recorder] +${entry.elapsed_ms.toFixed(2)}ms ${name} ${payload}`);
  } catch (_) {
    console.info(`[recorder] +${entry.elapsed_ms.toFixed(2)}ms ${name}`);
  }
  return entry;
}

export function pickWebmMime() {
  const candidates = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm'
  ];
  const sup = (t) => window.MediaRecorder?.isTypeSupported?.(t);
  const chosen = candidates.find((t) => {
    try { return sup?.(t); } catch (_) { return false; }
  });
  return chosen || 'video/webm';
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000, frameThreshold } = {}) {
  if (!(canvas instanceof HTMLCanvasElement)) {
    throw new TypeError('startRecorder ожидает HTMLCanvasElement');
  }
  const stream = canvas.captureStream?.(fps);
  if (!stream) throw new Error('canvas.captureStream() не поддерживается');

  const mimeType = pickWebmMime();
  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });

  const chunks = [];
  let startedAt = 0;
  const frames = {
    count: 0,
    logged: false,
    threshold: Math.max(3, frameThreshold ?? Math.ceil(Math.max(1, fps) / 6)),
  };

  rec.ondataavailable = (e) => {
    if (e?.data?.size) {
      chunks.push(e.data);
    }
  };
  rec.onstart = () => {
    startedAt = performance.now();
    chunks.length = 0;
  };

  markStage('start_record(canvas)', { fps, vbr, mimeType });

  function noteFrame(detail) {
    frames.count += 1;
    if (!frames.logged && frames.count >= frames.threshold) {
      frames.logged = true;
      markStage('frames_flowing', {
        frames: frames.count,
        fps,
        threshold: frames.threshold,
        ...(detail ?? {}),
      });
    }
    if (frames.count <= 5 || frames.count % 30 === 0) {
      console.debug('[recorder] frame heartbeat', { frames: frames.count });
    }
  }

  return {
    rec,
    chunks,
    mimeType,
    getStartedMs: () => startedAt,
    noteFrame,
    options: { fps, vbr },
  };
}

export async function stopAndDownload(recCtx, outName, { finalDelayMs = 300 } = {}) {
  const { rec, chunks, mimeType, getStartedMs } = recCtx;
  markStage('export', { name: outName, finalDelayMs, chunkCount: chunks.length });
  await new Promise((r) => setTimeout(r, finalDelayMs));

  const done = new Promise((resolve, reject) => {
    rec.onstop = async () => {
      try {
        const recordedMs = Math.max(0, performance.now() - getStartedMs());
        const raw = new Blob(chunks, { type: mimeType });

        let fixedBlob = raw;
        try {
          if (typeof window.webmDurationFix === 'function') {
            fixedBlob = await window.webmDurationFix(raw, recordedMs);
          }
        } catch (e) {
          console.warn('webmDurationFix failed, fallback to raw blob', e);
        }

        downloadBlob(fixedBlob, outName);
        markStage('saved', {
          name: outName,
          recorded_ms: recordedMs,
          blob_bytes: fixedBlob.size,
          mimeType: fixedBlob.type,
        });
        resolve(fixedBlob);
      } catch (err) {
        reject(err);
      }
    };
  });

  if (rec.state !== 'inactive') {
    rec.stop();
  } else {
    queueMicrotask(() => {
      try {
        rec.onstop?.(new Event('stop'));
      } catch (_) {
        /* no-op */
      }
    });
  }
  return await done;
}

export function downloadBlob(blob, filename = 'export.webm') {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename; // финальное имя всё равно задаёт render.py (save_as)
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function bindGlobalExport(recCtxOrFn, outName = 'export.webm', options) {
  if (typeof recCtxOrFn === 'function') {
    window.exportWebM = recCtxOrFn;
  } else {
    window.exportWebM = async () => stopAndDownload(recCtxOrFn, outName, options);
  }
  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn) btn.addEventListener('click', () => window.exportWebM());
}

ensureLog();
