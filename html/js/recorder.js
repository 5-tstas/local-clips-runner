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

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000 } = {}) {
  const stream = canvas.captureStream?.(fps);
  if (!stream) throw new Error('canvas.captureStream() не поддерживается');

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
      try {
        if (typeof window.webmDurationFix === 'function') {
          fixedBlob = await window.webmDurationFix(raw, recordedMs);
        }
      } catch (e) {
        console.warn('webmDurationFix failed, fallback to raw blob', e);
      }

      downloadBlob(fixedBlob, outName);
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
