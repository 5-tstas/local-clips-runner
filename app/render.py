# app/render.py
from __future__ import annotations
import asyncio, base64, json, re
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import ValidationError
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

from .models import Batch, Job, Output, validate_batch

# ----- пути в репозитории -----
REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = REPO_ROOT / "html"

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
    """Рендерит одну врезку в WEBM и возвращает путь к итоговому файлу."""
    html_name = HTML_BY_TYPE[job.type]
    html_path = HTML_DIR / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Нет HTML: {html_path}")

    # Сливаем глобальные цвета/шрифт, если не заданы в payload
    payload = job.payload.dict()
    for k in ("bgColor", "textColor", "fontFamily"):
        payload.setdefault(k, getattr(output, k))

    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = f"file://{html_path}?data={b64}"

    w, h = output.size
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_videos = out_dir / ".tmp_videos"
    tmp_videos.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()  # headless по умолчанию
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            record_video_dir=str(tmp_videos),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        await page.goto(url)

        # Подождём шрифты (если есть)
        try:
            await page.wait_for_function(
                "document.fonts ? document.fonts.ready.then(() => true) : true",
                timeout=5000,
            )
        except PWTimeoutError:
            pass

        # Ждём сигнал завершения или таймаут по длительности
        duration_ms = max(500, job.durationSec * 1000)
        try:
            await page.wait_for_function("window.__CLIP_DONE__ === true", timeout=duration_ms + 2000)
        except PWTimeoutError:
            # Фоллбек — просто ждём указанную длительность
            await page.wait_for_timeout(duration_ms)

        video = page.video  # объект Video (путь доступен после закрытия)
        await context.close()
        await browser.close()

    src = Path(await video.path())  # webm, записанный Playwright'ом
    dst = out_dir / _outfile_name(idx, job)
    src.replace(dst)  # переименуем в стабильное имя
    return dst

async def render_batch(batch: Batch, out_dir: Path) -> Path:
    """Рендерит все jobs, упаковывает их в ZIP и возвращает путь к ZIP."""
    validate_batch(batch)
    out_dir.mkdir(parents=True, exist_ok=True)

    outs: List[Path] = []
    for i, job in enumerate(batch.jobs, start=1):
        f = await render_job(i, job, batch.output, out_dir)
        outs.append(f)

    zip_path = out_dir / "clips.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for f in outs:
            zf.write(f, arcname=f.name)
    return zip_path

def load_batch(json_path: Path) -> Batch:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    return Batch(**data)

# Удобный синхронный вызов (для будущего GUI/сервера)
def run_batch(json_path: Path, out_dir: Path) -> Path:
    batch = load_batch(json_path)
    return asyncio.run(render_batch(batch, out_dir))
