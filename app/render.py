# app/render.py
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile, ZIP_DEFLATED

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

from models import Batch, Job, Output, validate_batch

# ----- пути -----
REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = REPO_ROOT / "html"

# соответствие тип → html-страница
HTML_BY_TYPE: Dict[str, str] = {
    "overlay": "overlay.html",
    "chat": "chat-typing.html",
    "abc": "abc-transist.html",
}

def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "clip"

def _outfile_name(idx: int, job: Job) -> str:
    return f"{idx:03d}_{job.type}_{_slug(job.name)}.webm"

async def render_job(idx: int, job: Job, output: Output, out_dir: Path) -> Path:
    """
    Рендерит одну врезку в WEBM через Playwright/Chromium.
    Возвращает путь к итоговому .webm
    """
    html_name = HTML_BY_TYPE[job.type]
    html_path = HTML_DIR / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Не найден HTML: {html_path}")

    # Слить глобальные стили с payload (локальные переопределяют глобальные)
    payload = job.payload.dict()
    for k in ("bgColor", "textColor", "fontFamily"):
        payload.setdefault(k, getattr(output, k))

    # Передаём STATE через base64(JSON) в query `?data=...`
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = html_path.as_uri() + "?data=" + b64

    w, h = output.size
    tmp_dir = out_dir / ".tmp_videos"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()  # headless по умолчанию
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            record_video_dir=str(tmp_dir),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        await page.goto(url)

        # Проставим фон и цвета до первого кадра (убирает «серый» старт)
await page.evaluate("""
(() => {
  const S = window.STATE || {};
  if (S.bgColor) document.body.style.background = S.bgColor;
  if (S.textColor) document.body.style.color = S.textColor;
})();
""")

# Явно запустим анимацию в зависимости от типа
launchers = {
    "overlay": ["preview", "runPreview", "start", "play"],
    "chat":    ["runPreview", "preview", "start", "play"],
    "abc":     ["preview", "start", "play"]
}
fns = launchers.get(job.type, ["preview","runPreview","start","play"])
await page.evaluate(
    """
    (names) => {
      const S = window.STATE || {};
      for (const name of names) {
        const fn = (window as any)[name];
        if (typeof fn === 'function') {
          try { fn(S); return true; } catch(_) {}
          try { fn(); return true; } catch(_) {}
        }
      }
      // Пытаемся кликнуть типичные кнопки предпросмотра
      const sels = ['#btnPreview','#preview','[data-action="preview"]','.preview','button'];
      for (const sel of sels) {
        const el = document.querySelector(sel);
        if (el) { (el as HTMLElement).click(); return true; }
      }
      return false;
    }
    """,
    fns
)


        # дождёмся шрифтов (если есть) — не критично
        try:
            await page.wait_for_function(
                "() => (document.fonts ? document.fonts.ready.then(() => true) : true)",
                timeout=5000,
            )
        except PWTimeoutError:
            pass

        # ждём флаг завершения или таймаут по durationSec
        duration_ms = max(500, job.durationSec * 1000)
        try:
            await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 2000)
        except PWTimeoutError:
            await page.wait_for_timeout(duration_ms)

        video = page.video  # доступ к записи появится после закрытия контекста
        await context.close()
        await browser.close()

    # после закрытия контекста файл появится на диске
    src = Path(await video.path())  # type: ignore[arg-type]
    dst = out_dir / _outfile_name(idx, job)
    src.replace(dst)
    return dst

async def render_batch(batch: Batch, out_dir: Path) -> Path:
    """
    Рендерит все jobs последовательно и возвращает путь к ZIP с .webm.
    """
    validate_batch(batch)
    out_dir.mkdir(parents=True, exist_ok=True)

    outs: List[Path] = []
    for i, job in enumerate(batch.jobs, start=1):
        path = await render_job(i, job, batch.output, out_dir)
        outs.append(path)

    zip_path = out_dir / "clips.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for f in outs:
            zf.write(f, arcname=f.name)
    return zip_path
