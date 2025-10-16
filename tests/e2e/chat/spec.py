from __future__ import annotations

import unittest

from tests.e2e.base import ExportTestCaseMixin
from tools.verify_webm import verify_webm


class TestChatSpec(ExportTestCaseMixin, unittest.TestCase):
    def test_chat_typing_generates_frames_and_valid_webm(self) -> None:
        page = self.make_page()
        self.goto_html(page, "chat-typing.html")

        page.wait_for_function("typeof window.renderFrame === 'function'")
        page.evaluate(
            """
            window.__FRAME_COUNT__ = 0;
            const orig = window.renderFrame;
            window.renderFrame = function(...args) {
              window.__FRAME_COUNT__ = (window.__FRAME_COUNT__ || 0) + 1;
              return orig.apply(this, args);
            };
            """
        )

        page.wait_for_function("document.querySelector('#exportBtn') !== null")

        with page.expect_download() as download_info:
            page.click("#exportBtn")
        download = download_info.value

        saved = self.save_download(download, "chat-typing.webm")

        frame_count = page.evaluate("window.__FRAME_COUNT__ || 0")
        self.assertGreater(frame_count, 30, "expected animation to render multiple frames")

        verify_webm(saved)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()

