# app/render.py
from __future__ import annotations
import base64, json, os, re, subprocess
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile, ZIP_DEFLATED

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from models import Batch, Job, Output, validate_batch

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_DIR   = REPO_ROOT / "html"

# КОРОТКИЕ имена html (как в архиве)
HTML_BY_TYPE: Dict[str, str] = {
    "overlay": "overlay.html",
    "chat":    "chat-typing.html",
    "abc":     "abc-transist.html",
}

# ---------- пост-хук (как в архиве) ----------
def _run_post_render_hook(out_dir: Path) -> None:
    hook = REPO_ROOT / "scripts" / "post_render_fix_webm.sh"
    if not hook.exists():
        print(f"[post-hook][warn] not found: {hook}")
        return
    env = dict(os.environ)
    env["OUT_DIR"] = str(out_dir)
    try:
        subprocess.run(["bash", "-e", str(hook)], check=True, env=env)
        print("[post-hook] done")
    except subprocess.CalledProcessError as e:
        print(f"[post-hook][warn] failed: {e}")

def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "clip"

def _outfile_name(idx: int, job: Job) -> str:
    return f"{idx:03d}_{job.type}_{_slug(job.name)}.webm"

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
    # запуск предпросмотра: функция → кнопка → клавиша
    for fn in ("runPreview", "startPreview", "preview"):
        try:
            ok = await page.evaluate(f"typeof window.{fn} === 'function'")
            if ok:
                await page.evaluate(f"window.{fn}()")
                return
        except Exception:
            pass
    for sel in ("#previewBtn", "#btnPreview", "button[data-action='preview']"):
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                return
        except Exception:
            pass
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass

async def _try_export(page, prefer_funcs: List[str], btn_ids: List[str]) -> bool:
    # Попытка запустить экспорт внутри страницы: глобальные функции → кнопка
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
}})();
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

    # Слить настройки output в payload
    payload = dict(job.payload or {})
    for k in ("size", "theme", "bgColor", "textColor", "fontFamily", "safeArea"):
        v = getattr(output, k, None)
        if v is not None and k not in payload:
            payload[k] = v

    # Передаём STATE как ?data=base64(JSON) и глушим автозапуск (?autostart=0)
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = html_path.as_uri() + "?autostart=0&data=" + b64

    w, h = output.size
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_videos = out_dir / ".tmp_videos"
    tmp_videos.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            accept_downloads=True,
            record_video_dir=tmp_videos.as_posix(),            # фолбэк-запись страницы
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_load_state("networkidle")

        # дождёмся загрузки шрифтов если есть
        try:
            await page.wait_for_function(
                "document.fonts && document.fonts.ready ? document.fonts.ready.then(() => true) : true",
                timeout=5000
            )
        except PWTimeoutError:
            pass

        # ===== тип-специфическая подготовка =====
        if job.type == "overlay":
            t  = payload.get("title") or ""
            st = payload.get("subtitle") or ""
            body = payload.get("body") or []
            if isinstance(body, list): body = "\n".join(str(x) for x in body)
            await _fill(page, "#title", t)
            await _fill(page, "#subtitle", st)
            await _fill(page, "#body", body)

            # 1) Попытка ЭКСПОРТА (как в архиве)
            got = False
            try:
                started = await _try_export(page, ["exportWebM"], ["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    got = True
            except Exception:
                got = False

            # 2) Фолбэк — запись
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
            # СНАЧАЛА пробуем экспорт из канваса (runExport), затем фолбэк на запись страницы.
            lines = payload.get("lines") or []
            md = "\n\n".join(lines) if isinstance(lines, list) else str(lines)
            await _fill(page, "#answer", md)
            if payload.get("prompt"):
                await _fill(page, "#prompt", payload.get("prompt"))

            # скорости/паузы/FPS/звук
            def _num(v, d):
                try: return int(v) if v is not None else d
                except: return d
            cps_prompt      = _num(payload.get("cpsPrompt"), 14)
            cps_answer      = _num(payload.get("cpsAnswer"), 20)
            pause_sentence  = _num(payload.get("pauseSentence"), 220)
            pause_comma     = _num(payload.get("pauseComma"), 110)
            fps             = _num(payload.get("fps"), 30)
            think_sec       = _num(payload.get("thinkSec"), 2)

            # КЛЮЧЕВОЕ: thinkSec в UI обычно не подхватывается из ?data= — проставим явно
            for sel, val in [
                ("#cpsPrompt", str(cps_prompt)),
                ("#cpsAnswer", str(cps_answer)),
                ("#pauseSentence", str(pause_sentence)),
                ("#pauseComma", str(pause_comma)),
                ("#fps", str(fps)),
                ("#thinkSec", str(think_sec)),
            ]:
                try:
                    await _fill(page, sel, val)
                except Exception:
                    pass
            try:
                await _fill(page, "#soundOn", "")  # без звука в headless
            except Exception:
                pass

            # 1) Первая попытка — экспорт WebM с канваса (chat-typing.html использует runExport)
            got = False
            try:
                started = await _try_export(
                    page,
                    ["runExport"],
                    ["#exportBtn","#btnExport","#export","button[data-action='export']"]
                )
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    got = True
            except Exception:
                got = False

            if got:
                await context.close(); await browser.close()
                return out_dir / _outfile_name(idx, job)

            # 2) Фолбэк — предпросмотр + запись страницы до конца
            await _start_preview(page, "chat")

            txt_prompt = payload.get("prompt") or ""
            text = (txt_prompt + "\n" + md)
            sent = len(re.findall(r"[.!?…]", text))
            comm = len(re.findall(r"[,;:]", text))
            est_ms = int(
                1000 * think_sec
                + (len(txt_prompt) * 1000) / max(1, cps_prompt)
                + (len(md) * 1000) / max(1, cps_answer)
                + sent * pause_sentence
                + comm * pause_comma
                + 1500
            )
            duration_ms = max(3000, min(est_ms, 120000))
            try:
                await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 2000)
            except PWTimeoutError:
                await page.wait_for_timeout(duration_ms)

            video = page.video
            await context.close(); await browser.close()
            src = Path(await video.path())  # type: ignore[arg-type]
            dst = out_dir / _outfile_name(idx, job)
            src.replace(dst); return dst

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

            got = False
            try:
                started = await _try_export(page, ["exportWebM"], ["#exportBtn","#btnExport","#export"])
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

# ---------- батч ----------
async def render_batch(batch: Batch, out_dir: Path) -> Path:
    validate_batch(batch)
    out_dir.mkdir(parents=True, exist_ok=True)

    outs: List[Path] = []
    for i, job in enumerate(batch.jobs, start=1):
        f = await render_job(i, job, batch.output, out_dir)
        outs.append(f)

    # пост-хук (как в архиве)
    _run_post_render_hook(out_dir)

    zip_path = out_dir / "clips.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for f in outs:
            zf.write(f, arcname=f.name)
    return zip_path






