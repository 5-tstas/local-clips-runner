# app/main.py
import threading, time, webbrowser
import uvicorn
from server import app  # локальный импорт из той же папки

# авто-скачивание браузера, если ещё не установлен
import sys, subprocess
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except Exception:
    pass


PORT = 7080
URL = f"http://127.0.0.1:{PORT}/"

def open_browser_later():
    time.sleep(0.8)
    try:
        webbrowser.open(URL)
    except Exception:
        pass

if __name__ == "__main__":
    threading.Thread(target=open_browser_later, daemon=True).start()
    uvicorn.run("app.server:app", host="127.0.0.1", port=PORT, reload=False, log_level="info")
