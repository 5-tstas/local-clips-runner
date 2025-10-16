// ./js/recorder.js
// Единый рекордер WebM (VP9/VP8), с проверками источника и фиксом duration через глобальный webm-duration-fix.

function reportStage(stage, detail) {
  try {
    window.__clipStage?.(stage, detail);
  } catch (err) {
    console.warn('telemetry stage callback failed', err);
  }
}

export function pickWebmMime() {
  const options = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm'
  ];
  const isSup = (t) => window.MediaRecorder?.isTypeSupported?.(t);
  return options.find(isSup) || 'video/webm';
}

export function ensureCanvas(canvasId, { width = 1280, height = 720 } = {}) {
  const el = document.getElementById(canvasId);
  if (!(el instanceof HTMLCanvasElement)) {
    throw new Error(`Canvas #${canvasId} не найден`);
  }
  if (typeof width === 'number' && el.width !== width) {
    throw new Error(`Canvas #${canvasId} ожидает ширину ${width}, получено ${el.width}`);
  }
  if (typeof height === 'number' && el.height !== height) {
    throw new Error(`Canvas #${canvasId} ожидает высоту ${height}, получено ${el.height}`);
  }
  return el;
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000 } = {}) {
  if (!(canvas instanceof HTMLCanvasElement)) {
    throw new Error('startRecorder ожидает HTMLCanvasElement');
  }

  const stream = canvas.captureStream?.(fps);
  if (!stream) throw new Error('canvas.captureStream() не поддерживается');

  const [videoTrack] = stream.getVideoTracks();
  if (!videoTrack) {
    throw new Error('canvas.captureStream() вернул поток без видео трека');
  }

  const settings = typeof videoTrack.getSettings === 'function' ? videoTrack.getSettings() : {};
  if (settings && settings.displaySurface) {
    console.error('Получен поток захвата экрана вместо canvas', settings);
    videoTrack.stop();
    reportStage('start_record_failed', { reason: 'displaySurface', settings });
    throw new Error('MediaRecorder получил displaySurface=' + settings.displaySurface);
  }

  if (settings.width && settings.width !== canvas.width) {
    console.warn('Размер потока не совпадает с canvas', settings.width, canvas.width);
  }
  if (settings.height && settings.height !== canvas.height) {
    console.warn('Высота потока не совпадает с canvas', settings.height, canvas.height);
  }

  const mimeType = pickWebmMime();
  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });

  const chunks = [];
  let startedAt = 0;

  rec.ondataavailable = (e) => {
    if (e.data && e.data.size) chunks.push(e.data);
  };
  rec.onstart = () => {
    startedAt = performance.now();
    chunks.length = 0;
  };

  reportStage('start_record', {
    canvasId: canvas.id || null,
    width: canvas.width,
    height: canvas.height,
    fps,
    mimeType,
    trackSettings: settings || null,
  });

  return { rec, chunks, mimeType, getStartedMs: () => startedAt };
}

export async function stopAndDownload(recCtx, outName, { finalDelayMs = 300 } = {}) {
  const { rec, chunks, mimeType, getStartedMs } = recCtx;
  await new Promise((r) => setTimeout(r, finalDelayMs));

  const done = new Promise((resolve) => {
    rec.onstop = async () => {
      const recordedMs = Math.max(0, performance.now() - getStartedMs());
      const raw = new Blob(chunks, { type: mimeType });

      let fixedBlob = raw;
      let usedFix = false;
      try {
        if (typeof window.webmDurationFix === 'function') {
          fixedBlob = await window.webmDurationFix(raw, recordedMs);
          usedFix = fixedBlob !== raw;
        }
      } catch (e) {
        console.warn('webmDurationFix failed, fallback to raw blob', e);
      }

      reportStage('export', {
        recordedMs,
        chunks: chunks.length,
        rawBytes: raw.size,
        usedFix
      });

      downloadBlob(fixedBlob, outName);
      reportStage('saved_request', {
        bytes: fixedBlob.size,
        mimeType,
        filename: outName,
      });
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
