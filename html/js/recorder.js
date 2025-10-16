// html/js/recorder.js
// Универсальный рекордер WebM (VP9→VP8) через canvas.captureStream + MediaRecorder.
// Требования: 1280x720@30fps, ~5 Mbps, корректная duration.
// Ожидается глобальная функция window.webmDurationFix(blob, durationMs) из html/vendor/webm-duration-fix.min.js.
// Если фиксер отсутствует/падает — используем raw-blob (лучше, чем null-duration).

export function pickWebmMime() {
  const candidates = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm'
  ];
  const sup = (t) => window.MediaRecorder?.isTypeSupported?.(t);
  return candidates.find((t) => {
    try { return sup(t); } catch (_) { return false; }
  }) || 'video/webm';
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000 } = {}) {
  if (!canvas || typeof canvas.captureStream !== 'function') {
    throw new Error('canvas.captureStream() не поддерживается');
  }
  const stream = canvas.captureStream(fps);
  if (!stream) throw new Error('Не удалось получить поток canvas.captureStream');

  const mimeType = pickWebmMime();
  const rec = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: vbr });

  const chunks = [];
  let startedAt = 0;

  rec.addEventListener('dataavailable', (ev) => {
    if (ev.data && ev.data.size) chunks.push(ev.data);
  });
  rec.addEventListener('start', () => {
    startedAt = performance.now();
  });

  return {
    rec,
    stream,
    chunks,
    mimeType,
    getRecordedMs: () => (startedAt ? Math.max(0, performance.now() - startedAt) : 0)
  };
}

export async function stopAndDownload(recCtx, outName = 'export.webm', { finalDelayMs = 300 } = {}) {
  if (!recCtx || !recCtx.rec) throw new Error('Recorder context is not valid');
  const { rec, chunks, mimeType, getRecordedMs } = recCtx;

  if (rec.state === 'inactive') {
    if (!chunks.length) return null;
    const rawInactive = new Blob(chunks, { type: mimeType });
    downloadBlob(rawInactive, outName);
    return rawInactive;
  }

  if (finalDelayMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, finalDelayMs));
  }

  const result = await new Promise((resolve) => {
    const handleStop = async () => {
      rec.removeEventListener('stop', handleStop);
      const durationMs = typeof getRecordedMs === 'function' ? getRecordedMs() : 0;
      const rawBlob = new Blob(chunks, { type: mimeType });
      let fixedBlob = rawBlob;
      try {
        if (typeof window.webmDurationFix === 'function') {
          const maybe = await window.webmDurationFix(rawBlob, durationMs);
          if (maybe) fixedBlob = maybe;
        }
      } catch (err) {
        console.warn('webmDurationFix failed, fallback to raw blob', err);
        fixedBlob = rawBlob;
      }
      downloadBlob(fixedBlob, outName);
      resolve(fixedBlob);
    };
    rec.addEventListener('stop', handleStop, { once: true });
  });

  rec.stop();
  return result;
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

// Привязывает глобальную функцию экспорта + кнопку (#exportBtn|#btnExport|#export).
// Вызывать после startRecorder() и запуска вашей анимации/печати.
export function bindGlobalExport(recCtx, outName = 'export.webm') {
  const stop = async () => {
    if (!recCtx || !recCtx.rec) return null;
    if (recCtx.rec.state === 'inactive' && !(recCtx.chunks?.length)) {
      return null;
    }
    return await stopAndDownload(recCtx, outName, { finalDelayMs: 300 });
  };

  window.exportWebM = stop;
  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn && !btn.dataset.recorderBound) {
    btn.dataset.recorderBound = '1';
    btn.addEventListener('click', () => window.exportWebM());
  }
  return stop;
}
