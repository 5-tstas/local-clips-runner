# app/server.py
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from models import Batch
from render import render_batch

APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR / "ui"
OUT_ROOT = Path.home() / "Movies" / "ClipsRunner"

app = FastAPI(title="Local Clips Runner", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

@app.get("/", response_class=HTMLResponse)
def index():
    return (UI_DIR / "index.html").read_text(encoding="utf-8")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/render")
async def render(json_file: UploadFile = File(...)):
    if not json_file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Загрузите JSON-файл батча.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    content = await json_file.read()
    try:
        data = json.loads(content.decode("utf-8"))
        batch = Batch(**data)  # старая схема: output + jobs[]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Некорректный JSON: {e}")

    (out_dir / "batch.json").write_bytes(content)

    try:
        zip_path = await render_batch(batch, out_dir)   # два аргумента
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename="clips.zip",
        headers={"Cache-Control": "no-store"},
    )
