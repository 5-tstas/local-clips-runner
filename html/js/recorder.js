// ./js/recorder.js
// Единый рекордер WebM (VP9/VP8), с фиксом duration через глобальный webm-duration-fix (если доступен).

export function pickWebmMime() {
  const options = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm'
  ];
  const isSup = (t) => window.MediaRecorder?.isTypeSupported?.(t);
  return options.find(isSup) || 'video/webm';
}

function ensureCanvas(canvas) {
  if (!(canvas instanceof HTMLCanvasElement)) {
    throw new Error('startRecorder ожидает HTMLCanvasElement');
  }
  if (typeof canvas.captureStream !== 'function') {
    throw new Error('canvas.captureStream() не поддерживается');
  }
}

function createTelemetry(canvas, track, targetFps) {
  const settings = typeof track.getSettings === 'function' ? (track.getSettings() || {}) : {};
  if (Object.prototype.hasOwnProperty.call(settings, 'displaySurface') && settings.displaySurface) {
    console.error('startRecorder: обнаружен displaySurface в настройках трека', settings);
    throw new Error('Источник записи должен быть canvas.captureStream, а не экран');
  }

  const telemetry = {
    canvasId: canvas.id || null,
    width: canvas.width,
    height: canvas.height,
    targetFps,
    trackSettings: settings,
    stage: 'init',
    startedAt: null,
    framesAt: null,
    stoppedAt: null,
    exportedAt: null,
    savedAt: null,
    framesFlowing: false,
    frameCount: 0
  };

  window.__RECORDER_STATE__ = telemetry;
  return telemetry;
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000 } = {}) {
  ensureCanvas(canvas);
  const stream = canvas.captureStream(fps);
  if (!(stream instanceof MediaStream)) {
    throw new Error('canvas.captureStream() вернул некорректный MediaStream');
  }

  const [track] = stream.getVideoTracks();
  if (!track || track.kind !== 'video') {
    throw new Error('canvas.captureStream() не вернул видеотрек');
  }

  const telemetry = createTelemetry(canvas, track, fps);

  const mimeType = pickWebmMime();
  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });

  const chunks = [];
  let waitForFramesResolve;
  const waitForFrames = new Promise((resolve) => {
    waitForFramesResolve = resolve;
  });

  let framesGuard = null;
  let rafActive = false;

  const markFramesFlowing = () => {
    if (telemetry.framesFlowing) return;
    telemetry.framesFlowing = true;
    telemetry.framesAt = performance.now();
    telemetry.stage = 'frames_flowing';
    window.__RECORDER_STATE__ = telemetry;
    if (framesGuard) clearTimeout(framesGuard);
    waitForFramesResolve(true);
  };

  const pumpFrames = () => {
    if (!rafActive) return;
    telemetry.frameCount += 1;
    try { track.requestFrame?.(); } catch (_) {}
    requestAnimationFrame(() => {
      markFramesFlowing();
      pumpFrames();
    });
  };

  rec.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) chunks.push(e.data);
  });

  rec.addEventListener('start', () => {
    telemetry.startedAt = performance.now();
    telemetry.stage = 'recording';
    telemetry.framesFlowing = false;
    window.__RECORDER_STATE__ = telemetry;
    chunks.length = 0;
    rafActive = true;
    requestAnimationFrame(markFramesFlowing);
    framesGuard = setTimeout(() => {
      if (!telemetry.framesFlowing) {
        console.error('MediaRecorder: кадры не стартовали за отведённое время');
        waitForFramesResolve(false);
      }
    }, 2000);
    pumpFrames();
  });

  rec.addEventListener('stop', () => {
    rafActive = false;
    telemetry.stoppedAt = performance.now();
    telemetry.stage = 'stopped';
    window.__RECORDER_STATE__ = telemetry;
  });

  return { rec, chunks, mimeType, telemetry, waitForFrames };
}

export async function stopAndDownload(recCtx, outName, { finalDelayMs = 300 } = {}) {
  const { rec, chunks, mimeType, telemetry } = recCtx;
  telemetry.stage = 'export_requested';
  telemetry.exportedAt = performance.now();
  window.__RECORDER_STATE__ = telemetry;

  await new Promise((r) => setTimeout(r, finalDelayMs));

  const done = new Promise((resolve) => {
    rec.onstop = async () => {
      const recordedMs = Math.max(0, (telemetry.startedAt && telemetry.stoppedAt)
        ? telemetry.stoppedAt - telemetry.startedAt
        : performance.now() - (telemetry.startedAt || performance.now()));
      const raw = new Blob(chunks, { type: mimeType });

      let fixedBlob = raw;
      try {
        if (typeof window.webmDurationFix === 'function') {
          fixedBlob = await window.webmDurationFix(raw, recordedMs);
        }
      } catch (e) {
        console.warn('webmDurationFix failed, fallback to raw blob', e);
      }

      telemetry.stage = 'blob_ready';
      window.__RECORDER_STATE__ = telemetry;

      downloadBlob(fixedBlob, outName);
      telemetry.stage = 'saved';
      telemetry.savedAt = performance.now();
      window.__RECORDER_STATE__ = telemetry;
      resolve(fixedBlob);
    };
  });

  rec.stop();
  return await done;
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

export function bindGlobalExport(recCtxOrFn, outName = 'export.webm', options) {
  if (typeof recCtxOrFn === 'function') {
    window.exportWebM = recCtxOrFn;
  } else {
    window.exportWebM = async () => stopAndDownload(recCtxOrFn, outName, options);
  }
  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn) btn.addEventListener('click', () => window.exportWebM());
}
