from __future__ import annotations

import contextlib
from pathlib import Path

from playwright.sync_api import Browser, Download, Page, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]
HTML_ROOT = REPO_ROOT / "html"
ARTIFACTS_ROOT = REPO_ROOT / "tests" / "e2e" / "artifacts"
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)


class ExportTestCaseMixin:
    browser: Browser

    @classmethod
    def setUpClass(cls) -> None:  # noqa: N802 (unittest compat)
        cls._pw_manager = sync_playwright()
        cls._pw = cls._pw_manager.start()
        try:
            cls.browser = cls._pw.chromium.launch(headless=True)
        except Exception:
            with contextlib.suppress(Exception):
                cls._pw.stop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:  # noqa: N802
        with contextlib.suppress(Exception):
            cls.browser.close()
        with contextlib.suppress(Exception):
            cls._pw.stop()

    def make_page(self) -> Page:
        context = self.browser.new_context(accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        page.add_init_script(
            """
(() => {
  const forbid = (name) => () => { throw new Error(`${name} is forbidden`); };
  if (navigator.mediaDevices) {
    navigator.mediaDevices.getDisplayMedia = forbid('displayMedia');
    navigator.mediaDevices.getUserMedia = forbid('userMedia');
  }
})()
"""
        )
        return page

    def save_download(self, download: Download, name: str) -> Path:
        target = ARTIFACTS_ROOT / name
        download.save_as(str(target))
        return target

    def goto_html(self, page: Page, filename: str) -> None:
        target = HTML_ROOT / filename
        page.goto(target.as_uri())

