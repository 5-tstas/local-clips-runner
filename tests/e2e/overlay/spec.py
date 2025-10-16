from __future__ import annotations

import unittest

from tests.e2e.base import ExportTestCaseMixin
from tools.verify_webm import verify_webm


class TestOverlaySpec(ExportTestCaseMixin, unittest.TestCase):
    def test_overlay_export_produces_valid_webm(self) -> None:
        page = self.make_page()
        self.goto_html(page, "overlay.html")
        page.wait_for_function("typeof window.exportWebM === 'function'")

        with page.expect_download() as download_info:
            page.evaluate("window.exportWebM()")
        download = download_info.value
        self.assertIsNone(download.failure())

        saved = self.save_download(download, "overlay.webm")
        info = verify_webm(saved)

        timeline = self.fetch_timeline(page)
        self.assert_stage_sequence(timeline)
        self.assert_no_banned_calls(page)
        self.record_summary('overlay', saved, info, timeline)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()

