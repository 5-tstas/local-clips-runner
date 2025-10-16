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
  // гарантированный поток кадров
  const stream = canvas.captureStream?.(fps);
  if (!stream) throw new Error('canvas.captureStream() не поддерживается');

  const mimeType = pickWebmMime();
  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });

  const chunks = [];
  let startedAt = 0;

  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstart = () => { startedAt = performance.now(); };

  return {
    rec,
    chunks,
    mimeType,
    getStartedMs: () => startedAt,
    getDurationMs: () => Math.max(0, performance.now() - startedAt),
  };
}

export async function stopAndDownload(recCtx, outName, { finalDelayMs = 300 } = {}) {
  if (!recCtx || !recCtx.rec) throw new Error('Recorder context отсутствует');
  const { rec, chunks, mimeType, getStartedMs } = recCtx;

  // небольшая задержка — гарантируем запись последнего кадра
  if (finalDelayMs > 0) await new Promise((r) => setTimeout(r, finalDelayMs));

  const done = new Promise((resolve) => {
    rec.onstop = async () => {
      const recordedMs = typeof getStartedMs === 'function'
        ? Math.max(0, performance.now() - getStartedMs())
        : 0;
      const raw = new Blob(chunks, { type: mimeType });

      // Фикс длительности, если глобальный fixer доступен
      let fixedBlob = raw;
      try {
        if (typeof window.webmDurationFix === 'function') {
          // ожидаемый интерфейс: webmDurationFix(blob, durationMs) -> Promise<Blob>
          const durationMs = recordedMs || recCtx.getDurationMs?.() || 0;
          fixedBlob = await window.webmDurationFix(raw, durationMs);
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

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function bindGlobalExport(recCtxOrFn, outName = 'export.webm') {
  const runExport = async () => {
    if (typeof recCtxOrFn === 'function') {
      return await recCtxOrFn(outName);
    }
    if (recCtxOrFn && typeof recCtxOrFn === 'object') {
      return await stopAndDownload(recCtxOrFn, outName, { finalDelayMs: 300 });
    }
    throw new Error('Не задан обработчик exportWebM');
  };

  window.exportWebM = () => runExport();

  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn && !btn.dataset.webmBound) {
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      window.exportWebM();
    });
    btn.dataset.webmBound = '1';
  }

  return runExport;
}
