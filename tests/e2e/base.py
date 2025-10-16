from __future__ import annotations

import contextlib
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import Browser, Download, Page, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
HTML_ROOT = REPO_ROOT / "html"
ARTIFACTS_ROOT = REPO_ROOT / "tests" / "e2e" / "artifacts"
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

SUMMARY_PATH = ARTIFACTS_ROOT / "webm-summary.json"


class ExportTestCaseMixin:
    browser: Browser
    _summaries: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def setUpClass(cls) -> None:  # noqa: N802 (unittest compat)
        cls._pw_manager = sync_playwright()
        cls._pw = cls._pw_manager.start()
        try:
            cls.browser = cls._pw.chromium.launch(headless=True)
        except Exception as exc:
            with contextlib.suppress(Exception):
                cls._pw.stop()
            message = str(exc)
            if "Executable doesn't exist" in message:
                raise unittest.SkipTest(
                    "Playwright Chromium is missing. Run `playwright install chromium`."
                ) from exc
            raise

    @classmethod
    def tearDownClass(cls) -> None:  # noqa: N802
        with contextlib.suppress(Exception):
            if getattr(cls, "browser", None):
                cls.browser.close()
        with contextlib.suppress(Exception):
            cls._pw.stop()

    def make_page(self) -> Page:
        context = self.browser.new_context(accept_downloads=True)
        context.add_init_script(
            """
(() => {
  window.__RECORDER_LOG__ = [];
  window.__RECORDER_T0__ = undefined;
  window.__BANNED_API_CALLS__ = [];

  const guard = (target, key) => {
    if (!target) return;
    const orig = target[key];
    if (typeof orig !== 'function') return;
    target[key] = (...args) => {
      window.__BANNED_API_CALLS__.push(key);
      throw new Error(`Banned API: ${key}`);
    };
  };

  const tryPath = (path, key) => {
    const parts = path.split('.');
    let ref = window;
    for (const part of parts) {
      if (part === 'window') continue;
      ref = ref?.[part];
      if (!ref) return;
    }
    guard(ref, key);
  };

  tryPath('navigator.mediaDevices', 'getDisplayMedia');
  tryPath('navigator.mediaDevices', 'getUserMedia');
  tryPath('navigator', 'getUserMedia');
  tryPath('chrome.desktopCapture', 'chooseDesktopMedia');
  tryPath('chrome.desktopCapture', 'cancelChooseDesktopMedia');
})();
"""
        )
        self.addCleanup(context.close)
        page = context.new_page()
        return page

    def save_download(self, download: Download, name: str) -> Path:
        target = ARTIFACTS_ROOT / name
        download.save_as(str(target))
        return target

    def goto_html(self, page: Page, filename: str) -> None:
        target = HTML_ROOT / filename
        page.goto(target.as_uri())

    def fetch_timeline(self, page: Page) -> List[Dict[str, Any]]:
        return page.evaluate("window.__RECORDER_LOG__ || []")

    def assert_no_banned_calls(self, page: Page) -> None:
        calls = page.evaluate("window.__BANNED_API_CALLS__ || []")
        if calls:
            raise AssertionError(f"Banned APIs invoked: {calls}")

    def assert_stage_sequence(self, timeline: List[Dict[str, Any]], max_elapsed_ms: float = 20_000.0) -> None:
        if not timeline:
            raise AssertionError("Timeline is empty")
        names = [entry.get("name") for entry in timeline]
        expected = ['goto', 'init', 'start_record(canvas)', 'frames_flowing', 'export', 'saved']
        missing = [name for name in expected if name not in names]
        if missing:
            raise AssertionError(f"Missing stages: {missing}; timeline={names}")
        positions = {name: names.index(name) for name in expected}
        for earlier, later in zip(expected, expected[1:]):
            if positions[earlier] >= positions[later]:
                raise AssertionError(f"Stage order incorrect: {earlier} >= {later}")
        elapsed = [float(entry.get('elapsed_ms', 0)) for entry in timeline]
        if any(elapsed[i] > elapsed[i + 1] for i in range(len(elapsed) - 1)):
            raise AssertionError(f"Timeline is not monotonic: {elapsed}")
        if elapsed[-1] > max_elapsed_ms:
            raise AssertionError(f"Timeline exceeded limit ({elapsed[-1]} ms > {max_elapsed_ms} ms)")

        frames_entry = next((e for e in timeline if e.get('name') == 'frames_flowing'), None)
        if not frames_entry or (frames_entry.get('detail') or {}).get('frames', 0) <= 0:
            raise AssertionError(f"frames_flowing entry missing frames detail: {frames_entry}")
        saved_entry = next((e for e in timeline if e.get('name') == 'saved'), None)
        if not saved_entry or (saved_entry.get('detail') or {}).get('blob_bytes', 0) <= 0:
            raise AssertionError(f"saved entry missing blob detail: {saved_entry}")

    def record_summary(
        self,
        key: str,
        download_path: Path,
        verification: Dict[str, Any],
        timeline: List[Dict[str, Any]],
    ) -> None:
        data = {
            **verification,
            'artifact': str(download_path),
            'size_bytes': download_path.stat().st_size,
            'timeline': timeline,
        }
        self._summaries[key] = data
        SUMMARY_PATH.write_text(json.dumps(self._summaries, indent=2, ensure_ascii=False), encoding='utf-8')
        total_elapsed = 0.0
        for item in self._summaries.values():
            tl = item.get('timeline') or []
            if tl:
                total_elapsed += float(tl[-1].get('elapsed_ms', 0.0))
        if total_elapsed > 300_000:
            raise AssertionError(f"Total render time exceeded limit: {total_elapsed} ms")

