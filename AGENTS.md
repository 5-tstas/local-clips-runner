# AGENTS.md

Репозиторий: local-clips-runner --- генератор «врезок» для видео.
Фронтенд: статические страницы в каталоге html/:

html/overlay.html

html/abc-transist.html

html/chat-typing.html

Бэкенд рендера/автоматизации: app/render.py, app/server.py, run_local.py
(Playwright). Протокол с рендером не менять: Playwright ждёт глобальную
функцию window.exportWebM() и/или кнопку экспорта #exportBtn /
#btnExport / #export и перехватывает браузерный download.

Исходное условие (уже добавлено вручную на всех трёх страницах, перед
```{=html}
</head>
```
):

```{=html}
<!-- общие утилиты записи WebM -->
```
```{=html}
<script type="module" src="./js/recorder.js"></script>
```
```{=html}
<!-- опциональный фиксер длительности (даже заглушка не повредит) -->
```
```{=html}
<script src="./vendor/webm-duration-fix.min.js"></script>
```
Ожидаемые пути файлов: html/js/recorder.js,
html/vendor/webm-duration-fix.min.js. Важно: агент начинает работу с
шага 2 (ниже). Добавленные теги в
```{=html}
<head>
```
оставляем без изменений.

Инварианты

Только WebM: кодек VP9 (фолбэк VP8). Никакого MP4.

Кадр 1280×720, 30 FPS, видеобитрейт \~ 5 Мбит/с.

Duration в контейнере WebM должен быть корректным (исправлять фиксером).

Имена выходных файлов задаёт Python-рендер в app/render.py, например:

f"{idx:03d}*{job.type}*{slug(job.name)}.webm"

→ «временные» имена на фронте значения не имеют (Playwright переименует
при save_as(...)).

Совместимость с Playwright: должны существовать window.exportWebM()
и/или кнопка #export\*, инициирующая браузерный download.

Acceptance (готовность)

Каждая из трёх страниц стабильно создаёт скачиваемый WebM; Playwright
ловит download и сохраняет.

В метаданных файла ненулевая корректная длительность.

Файлы проигрываются в браузере и без ошибок загружаются в aistudios.com.

На chat-typing устранён баг «серый кадр/зацикливание»: кадры
обновляются, текст печатается, запись корректно завершается.

В репозитории отсутствуют любые ветки экспорта в .mp4.

Установка / запуск локально \# Python 3.11+ python -m venv .venv &&
source .venv/bin/activate pip install -r requirements.txt python -m
playwright install chromium

# старт локального сервера

python run_local.py \# откроется http://127.0.0.1:7080/

Политика PR

Conventional Commits (fix:, feat:, chore: ...).

В описании PR: проблема → решение → как проверить (URL страницы, что
нажать, что ожидается).

Не добавлять зависимости без обоснования; не менять форматирование «ради
форматирования».

Ограничения вывода (WebM-only)

MIME-выбор по приоритету: video/webm;codecs=vp9 → video/webm;codecs=vp8
→ video/webm.

Параметры: 1280×720 @ 30 fps, videoBitsPerSecond ≈ 5_000_000.

Фикс длительности: глобальная функция из
html/vendor/webm-duration-fix.min.js (при сбое --- безопасный фолбэк на
сырой Blob).

Задачи для агента 1) (Пропустить --- уже сделано вручную)

Теги
```{=html}
<script> в <head> на всех трёх страницах уже добавлены (см. «Контекст»). Ничего не менять.

2) Создать/перезаписать модуль записи: html/js/recorder.js
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
  return candidates.find(sup) || 'video/webm';
}

export function startRecorder(canvas, { fps = 30, vbr = 5_000_000 } = {}) {
  if (!canvas || typeof canvas.captureStream !== 'function') {
    throw new Error('recorder: canvas.captureStream() unsupported or canvas missing');
  }
  const stream = canvas.captureStream(fps);
  const mimeType = pickWebmMime();

  const rec = new MediaRecorder(stream, {
    mimeType,
    videoBitsPerSecond: vbr
  });

  const chunks = [];
  let startedAt = 0;

  rec.ondataavailable = (e) => {
    if (e && e.data && e.data.size) chunks.push(e.data);
  };
  rec.onstart = () => { startedAt = performance.now(); };

  return {
    rec,
    chunks,
    mimeType,
    getStartedMs: () => startedAt
  };
}

export async function stopAndDownload(recCtx, outName = 'export.webm', { finalDelayMs = 300 } = {}) {
  // небольшая пауза — гарантировать запись последнего кадра
  await new Promise(r => setTimeout(r, finalDelayMs));

  const { rec, chunks, mimeType, getStartedMs } = recCtx;
  if (!rec) throw new Error('recorder: stop called before start');

  const done = new Promise((resolve) => {
    rec.onstop = async () => {
      const recordedMs = Math.max(0, performance.now() - (getStartedMs?.() || performance.now()));
      const raw = new Blob(chunks, { type: mimeType });
      let fixed = raw;

      try {
        if (typeof window.webmDurationFix === 'function') {
          fixed = await window.webmDurationFix(raw, recordedMs);
        }
      } catch (err) {
        console.warn('[recorder] webmDurationFix failed, use raw blob', err);
      }

      // Инициируем браузерный download; Playwright перехватит и переименует
      downloadBlob(fixed, outName);
      resolve(fixed);
    };
  });

  rec.stop();
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

// Привязывает глобальную функцию экспорта + кнопку (#exportBtn|#btnExport|#export).
// Вызывать после startRecorder() и запуска вашей анимации/печати.
export function bindGlobalExport(recCtx, outName = 'export.webm') {
  window.exportWebM = async () => {
    await stopAndDownload(recCtx, outName, { finalDelayMs: 300 });
  };
  const btn = document.querySelector('#exportBtn, #btnExport, #export');
  if (btn) btn.addEventListener('click', () => window.exportWebM());
}

3) Перевести все три страницы на общий модуль записи

Для каждой из: html/overlay.html, html/abc-transist.html, html/chat-typing.html:

Найти существующие места, где создаются MediaRecorder(...) и/или вызывается canvas.captureStream(...).

Удалить локальную реализацию записи и заменить на вызовы из recorder.js, сохранив остальную логику страницы (отрисовка, UI, ввод и т. п.).

Мини-вставка (после инициализации canvas, перед стартом вашей анимации):

<script type="module">
  import { startRecorder, bindGlobalExport } from './js/recorder.js';

  const canvas = document.querySelector('canvas'); // при необходимости заменить селектор
  const recCtx = startRecorder(canvas, { fps: 30, vbr: 5_000_000 });
  recCtx.rec.start(); // начать запись после первого кадра

  // ... здесь остаётся существующая анимация/рендер ...

  bindGlobalExport(recCtx); // экспорт по window.exportWebM() и/или кнопке #export*
</script>
```
4)  Починить chat-typing (устранить «серый кадр» и «зацикливание»)

Печатать текст через requestAnimationFrame (или шаг \~16 мс), чтобы
каждый тик давал новый кадр.

Исключить длинные синхронные циклы/блокировку.

После вывода последнего символа --- пауза 200--300 мс, затем
window.exportWebM() (или клик по кнопке).

Каркас (адаптировать под фактические функции страницы):

```{=html}
<script type="module">
  import { startRecorder, bindGlobalExport } from './js/recorder.js';

  const canvas = document.querySelector('canvas');
  const ctx = canvas.getContext('2d');

  const recCtx = startRecorder(canvas, { fps: 30, vbr: 5_000_000 });
  recCtx.rec.start();

  const text = (window.STATE?.text || '').toString();
  let i = 0;

  function drawFrame(visible) {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    // фон/UI по логике страницы
    ctx.fillStyle = '#fff';
    ctx.font = '24px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    // TODO: если есть своя функция переноса строк — используйте её
    ctx.fillText(visible, 32, 64);
  }

  function tick() {
    drawFrame(text.slice(0, i));
    i++;
    if (i <= text.length) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  bindGlobalExport(recCtx);
</script>
```
Known issues (фиксировать в PR)

chat-typing: первый кадр серый/«вечное колесо» --- нет регулярного
обновления кадров или поток блокируется. Решение: RAF-цикл, кадр за тик,
затем экспорт.

duration = 0 или «0.00 c» --- не применён фикс длительности. Решение:
window.webmDurationFix(blob, ms) + микропаузa перед rec.stop().

aistudios отклоняет файл --- проверить MIME video/webm (VP9/VP8),
корректный duration, 1280×720@30fps; аудио-дорожка по умолчанию не
нужна, добавлять «тихий» канал только если платформа строго требует.

Шаблон запроса для Codex

Fix WebM generation across html/overlay.html, html/abc-transist.html,
html/chat-typing.html using html/js/recorder.js:

Overwrite html/js/recorder.js with the code from AGENTS.md.

On each page, replace any local MediaRecorder/canvas.captureStream with
startRecorder(...) + rec.start() + bindGlobalExport(...); keep
page-specific rendering intact.

For chat-typing, implement typing via requestAnimationFrame (no blocking
loops); end with \~300 ms guard, then export.

Only WebM (VP9→VP8), 1280×720@30 fps, \~5 Mbps; fix duration via
webm-duration-fix.min.js.

Do not change Python naming/paths; Playwright will rename via
save_as(...).
