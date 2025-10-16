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

# ---------- утилиты ----------
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

async def _export_via_download(page, timeout_ms: int):
    await page.wait_for_function("typeof window.exportWebM === 'function'", timeout=timeout_ms)
    async with page.expect_download(timeout=timeout_ms) as dl:
        await page.evaluate("window.exportWebM()")
    download = await dl.value

    try:
        await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=timeout_ms)
    except PWTimeoutError:
        # страница могла не ставить флаг, но загрузка прошла
        pass
    return download

async def _save_download(download, dst: Path) -> Path:
    failure = await download.failure()
    if failure:
        raise RuntimeError(f"Экспорт WebM не удался: {failure}")
    await download.save_as(dst.as_posix())
    return dst

# ---------- основной рендер ----------
async def render_job(idx: int, job: Job, output: Output, out_dir: Path) -> Path:
    html_name = HTML_BY_TYPE[job.type]
    html_path = HTML_DIR / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Не найден HTML: {html_path}")

    # Слить глобальные стили (output) в payload (локальные перекрывают)
    payload = job.payload.dict()
    for k in ("bgColor","textColor","fontFamily","cpsPrompt","cpsAnswer","pauseSentence","pauseComma","fps","soundOn","thinkSec"):
        v = getattr(output, k, None)
        if v is not None and k not in payload:
            payload[k] = v

    # Передаём STATE как base64(JSON) и глушим автостарт (?autostart=0)
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = html_path.as_uri() + "?autostart=0&data=" + b64

    w, h = output.size
    out_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            accept_downloads=True,
        )
        page = await context.new_page()
        try:
            await page.goto(url)

            await page.evaluate("""(() => {
              const S = (window.STATE||{});
              if (S.bgColor)  document.body.style.background = S.bgColor;
              if (S.textColor)document.body.style.color      = S.textColor;
            })()""")

            try:
                await page.wait_for_function(
                    "() => (document.fonts ? document.fonts.ready.then(() => true) : true)",
                    timeout=5000
                )
            except PWTimeoutError:
                pass

            base_timeout = max(15_000, job.durationSec * 1000 + 5_000)

            if job.type == "overlay":
                t  = payload.get("title") or ""
                st = payload.get("subtitle") or ""
                body = payload.get("body") or []
                if isinstance(body, list):
                    body = "\n".join(str(x) for x in body)
                await _fill(page, "#title", t)
                await _fill(page, "#subtitle", st)
                await _fill(page, "#body", body)

                download = await _export_via_download(page, base_timeout)
                dst = out_dir / _outfile_name(idx, job)
                return await _save_download(download, dst)

            if job.type == "chat":
                lines = payload.get("lines") or []
                md = "\n\n".join(lines) if isinstance(lines, list) else str(lines)
                await _fill(page, "#answer", md)
                if payload.get("prompt"):
                    await _fill(page, "#prompt", payload.get("prompt"))

                def _num(v, d):
                    try:
                        return int(v) if v is not None else d
                    except Exception:
                        return d

                cps_prompt = _num(payload.get("cpsPrompt"), 14)
                cps_answer = _num(payload.get("cpsAnswer"), 20)
                pause_sentence = _num(payload.get("pauseSentence"), 220)
                pause_comma    = _num(payload.get("pauseComma"), 110)
                fps            = _num(payload.get("fps"), 30)
                think_sec      = _num(payload.get("thinkSec"), 2)

                for sel, val in [
                    ("#cpsPrompt", str(cps_prompt)),
                    ("#cpsAnswer", str(cps_answer)),
                    ("#pauseSentence", str(pause_sentence)),
                    ("#pauseComma", str(pause_comma)),
                    ("#fps", str(fps)),
                ]:
                    await _fill(page, sel, val)
                await _fill(page, "#soundOn", "")

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
                timeout_ms = max(base_timeout, min(est_ms + 4000, 180_000))

                download = await _export_via_download(page, timeout_ms)
                dst = out_dir / _outfile_name(idx, job)
                return await _save_download(download, dst)

            # ABC
            images = payload.get("images") or []
            if not (isinstance(images, list) and len(images) >= 3):
                raise ValueError("Для abc нужно 3 файла в payload.images")

            def _abs(p: str) -> Path:
                pt = Path(p)
                return pt if pt.is_absolute() else (REPO_ROOT / p).resolve()

            await _set_file(page, "#fA", _abs(images[0]))
            await _set_file(page, "#fB", _abs(images[1]))
            await _set_file(page, "#fC", _abs(images[2]))

            download = await _export_via_download(page, base_timeout)
            dst = out_dir / _outfile_name(idx, job)
            return await _save_download(download, dst)

        finally:
            await context.close()
            await browser.close()

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




