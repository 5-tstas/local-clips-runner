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

  return { rec, chunks, mimeType, getStartedMs: () => startedAt };
}

export async function stopAndDownload({ rec, chunks, mimeType, getStartedMs }, outName, { finalDelayMs = 300 } = {}) {
  // небольшая задержка — гарантируем запись последнего кадра
  await new Promise(r => setTimeout(r, finalDelayMs));

  const done = new Promise((resolve) => {
    rec.onstop = async () => {
      const recordedMs = Math.max(0, performance.now() - getStartedMs());
      const raw = new Blob(chunks, { type: mimeType });

      // Фикс длительности, если глобальный fixer доступен
      let fixedBlob = raw;
      try {
        if (typeof window.webmDurationFix === 'function') {
          // ожидаемый интерфейс: webmDurationFix(blob, durationMs) -> Promise<Blob>
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

// Привязывает глобальный экспорт и кнопку (#exportBtn | #btnExport | #export)
export function bindGlobalExport(recCtx, outName = 'export.webm', opts) {
  const { finalDelayMs = 300 } = opts || {};
  window.exportWebM = async () => {
    return stopAndDownload(recCtx, outName, { finalDelayMs });
  };

  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn && !btn.dataset.webmClickBound) {
    btn.dataset.webmClickBound = '1';
    btn.addEventListener('click', () => {
      window.exportWebM?.();
    });
  }

  return window.exportWebM;
}
