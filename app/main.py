# app/main.py
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from server import app  # локальный импорт из той же папки

PORT = 7080
URL = f"http://127.0.0.1:{PORT}/"
HEALTH_URL = f"{URL}health"

logger = logging.getLogger("app.main")


def ensure_playwright_installed(timeout: int = 5) -> None:
    """Best-effort guard that avoids blocking startup on Playwright installs."""
    base_cmd = [sys.executable, "-m", "playwright", "install"]
    check_cmd = [*base_cmd, "--check", "chromium"]
    try:
        check_proc = subprocess.run(
            check_cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        if check_proc.returncode == 0:
            logger.debug("Playwright Chromium already installed")
            return
        logger.info(
            "Playwright Chromium check exited with code %s — attempting install (timeout %ss)",
            check_proc.returncode,
            timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Playwright install check timed out after %ss; skipping auto-install to keep startup responsive",
            timeout,
        )
        return
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Playwright install check failed: %s", exc)

    install_cmd = [*base_cmd, "chromium"]
    try:
        subprocess.run(
            install_cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Playwright install timed out after %ss; continuing without waiting",
            timeout,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Playwright install failed: %s", exc)


def should_open_browser() -> bool:
    flag = os.getenv("OPEN_BROWSER", "1").lower()
    return flag not in {"0", "false", "no", "off"}


def open_browser_later() -> None:
    time.sleep(0.8)
    try:
        webbrowser.open(URL)
    except Exception:  # pragma: no cover - defensive guard
        logger.debug("Unable to open browser automatically", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_playwright_installed()

    if should_open_browser():
        threading.Thread(target=open_browser_later, daemon=True).start()
    else:
        logger.info("Skipping automatic browser launch because OPEN_BROWSER=%s", os.getenv("OPEN_BROWSER"))

    logger.info("Local Clips Runner available at %s (health: %s)", URL, HEALTH_URL)
    uvicorn.run(app, host="127.0.0.1", port=PORT, reload=False, log_level="info")
