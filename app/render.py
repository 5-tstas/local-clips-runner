# app/render.py
from __future__ import annotations
import base64, json, re
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile, ZIP_DEFLATED

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from models import Batch, Job, Output, validate_batch

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_DIR   = REPO_ROOT / "html"

# КОРОТКИЕ имена html
HTML_BY_TYPE: Dict[str, str] = {
    "overlay": "overlay.html",
    "chat":    "chat-typing.html",
    "abc":     "abc-transist.html",
}

def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "clip"

def _outfile_name(idx: int, job: Job) -> str:
    return f"{idx:03d}_{job.type}_{_slug(job.name)}.webm"

# ---------- утилиты ----------
async def _fill(page, selector: str, value) -> None:
    el = await page.query_selector(selector)
    if not el or value is None:
        return
    t = (await el.get_attribute("type") or "").lower()
    if t == "checkbox":
        cur = await el.is_checked()
        want = bool(value)
        if want and not cur: await el.check()
        if (not want) and cur: await el.uncheck()
        return
    try:
        await el.fill(str(value))
    except Exception:
        try:
            await page.select_option(selector, str(value))
        except Exception:
            pass

async def _set_file(page, selector: str, path: Path) -> bool:
    el = await page.query_selector(selector)
    if not el or not path.exists():
        return False
    await el.set_input_files(path.as_posix())
    return True

async def _start_preview(page, job_type: str) -> None:
    # автозапуск для записи экрана (fallback)
    launchers = {
        "overlay": ["preview", "runPreview", "start", "play"],
        "chat":    ["runPreview", "preview", "start", "play"],
        "abc":     ["preview", "start", "play"],
    }.get(job_type, ["preview","runPreview","start","play"])
    await page.evaluate("""(names) => {
      const S = (window.STATE||{});
      for (const name of names) {
        const fn = window[name];
        if (typeof fn === 'function') {
          try { fn(S); return true; } catch(_) {}
          try { fn();  return true; } catch(_) {}
        }
      }
      const sels = ['#btnPreview','#preview','[data-action="preview"]','.preview','button'];
      for (const sel of sels) { const el = document.querySelector(sel); if (el) { el.click(); return true; } }
      return false;
    }""", launchers)

async def _try_export(page, prefer_funcs: List[str], btn_ids: List[str]) -> bool:
    # Пытаемся запустить экспорт WebM без предпросмотра
    code = f"""
(() => {{
  const tryFns = {json.dumps(prefer_funcs)};
  for (const name of tryFns) {{
    const fn = window[name];
    if (typeof fn === 'function') {{ try {{ fn(); return true; }} catch(_){{
    }} }}
  }}
  const ids = {json.dumps(btn_ids)};
  for (const id of ids) {{
    const el = document.querySelector(id);
    if (el) {{ el.click(); return true; }}
  }}
  return false;
}})()
"""
    try:
        return bool(await page.evaluate(code))
    except Exception:
        return False

# ---------- основной рендер ----------
async def render_job(idx: int, job: Job, output: Output, out_dir: Path) -> Path:
    html_name = HTML_BY_TYPE[job.type]
    html_path = HTML_DIR / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Не найден HTML: {html_path}")

    # Слить глобальные стили (output) в payload (локальные перекрывают)
    payload = job.payload.dict()
    for k in ("bgColor", "textColor", "fontFamily"):
        payload.setdefault(k, getattr(output, k))

    # Передаём STATE как base64(JSON) на случай если страница умеет читать query
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = html_path.as_uri() + "?data=" + b64

    w, h = output.size
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_videos = out_dir / ".tmp_videos"
    tmp_videos.mkdir(exist_ok=True)

    async with async_playwright() as p:
        # всегда включаем запись (на случай фолбэка)
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            accept_downloads=True,
            record_video_dir=str(tmp_videos),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        await page.goto(url)

        # убрать «серый» старт
        await page.evaluate("""(() => {
          const S = (window.STATE||{});
          if (S.bgColor)  document.body.style.background = S.bgColor;
          if (S.textColor)document.body.style.color      = S.textColor;
        })()""")

        # дождёмся шрифтов (если есть)
        try:
            await page.wait_for_function(
                "() => (document.fonts ? document.fonts.ready.then(() => true) : true)",
                timeout=5000
            )
        except PWTimeoutError:
            pass

        # ===== тип-специфическая подготовка =====
        if job.type == "overlay":
            # поля overlay: #title, #subtitle, #body
            t  = payload.get("title") or ""
            st = payload.get("subtitle") or ""
            body = payload.get("body") or []
            if isinstance(body, list): body = "\n".join(str(x) for x in body)
            await _fill(page, "#title", t)
            await _fill(page, "#subtitle", st)
            await _fill(page, "#body", body)

            # попытка экспорта (без preview), иначе — запись
            got = False
            try:
                started = await _try_export(page,
                    prefer_funcs=["exportWebM"], btn_ids=["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    got = True
            except Exception:
                got = False

            if not got:
                await _start_preview(page, "overlay")
                duration_ms = max(500, job.durationSec * 1000)
                try:
                    await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 3000)
                except PWTimeoutError:
                    await page.wait_for_timeout(duration_ms)
                video = page.video
                await context.close(); await browser.close()
                src = Path(await video.path())  # type: ignore[arg-type]
                dst = out_dir / _outfile_name(idx, job)
                src.replace(dst); return dst

            await context.close(); await browser.close()
            return out_dir / _outfile_name(idx, job)

        elif job.type == "chat":
            # поля chat: #answer (+ #prompt), параметры печати, звук off
            lines = payload.get("lines") or []
            answer = "\n\n".join(str(x) for x in lines) if isinstance(lines, list) else str(lines)
            await _fill(page, "#answer", answer)
            if payload.get("prompt"): await _fill(page, "#prompt", payload.get("prompt"))
            for sel, val in [("#cpsPrompt","14"),("#cpsAnswer","20"),
                             ("#thinkSec","2"),("#pauseSentence","220"),("#pauseComma","110"),("#fps","30")]:
                await _fill(page, sel, val)
            await _fill(page, "#soundOn", "")  # выключить

            # экспорта хватит (runExport), если нет — запись
            got = False
            try:
                started = await _try_export(page,
                    prefer_funcs=["runExport"], btn_ids=["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    got = True
            except Exception:
                got = False

            if not got:
                await _start_preview(page, "chat")  # runPreview() / кнопка
                duration_ms = max(500, job.durationSec * 1000)
                try:
                    await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 3000)
                except PWTimeoutError:
                    await page.wait_for_timeout(duration_ms)
                video = page.video
                await context.close(); await browser.close()
                src = Path(await video.path())  # type: ignore[arg-type]
                dst = out_dir / _outfile_name(idx, job)
                src.replace(dst); return dst

            await context.close(); await browser.close()
            return out_dir / _outfile_name(idx, job)

        else:  # ABC
            images = payload.get("images") or []
            if not (isinstance(images, list) and len(images) >= 3):
                raise ValueError("Для abc нужно 3 файла в payload.images")
            def _abs(p: str) -> Path:
                pt = Path(p)
                return pt if pt.is_absolute() else (REPO_ROOT / p).resolve()
            await _set_file(page, "#fA", _abs(images[0]))
            await _set_file(page, "#fB", _abs(images[1]))
            await _set_file(page, "#fC", _abs(images[2]))

            # сначала экспорт…
            got = False
            try:
                started = await _try_export(page,
                    prefer_funcs=["exportWebM"], btn_ids=["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    got = True
            except Exception:
                got = False

            if not got:
                # …если нет — запись
                await _start_preview(page, "abc")
                duration_ms = max(500, job.durationSec * 1000)
                try:
                    await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 3000)
                except PWTimeoutError:
                    await page.wait_for_timeout(duration_ms)
                video = page.video
                await context.close(); await browser.close()
                src = Path(await video.path())  # type: ignore[arg-type]
                dst = out_dir / _outfile_name(idx, job)
                src.replace(dst); return dst

            await context.close(); await browser.close()
            return out_dir / _outfile_name(idx, job)

async def render_batch(batch: Batch, out_dir: Path) -> Path:
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



