from __future__ import annotations

import unittest

from tests.e2e.base import ExportTestCaseMixin
from tools.verify_webm import verify_webm


class TestABCSpec(ExportTestCaseMixin, unittest.TestCase):
    def test_abc_transition_export_is_valid(self) -> None:
        page = self.make_page()
        self.goto_html(page, "abc-transist.html")
        page.wait_for_function("typeof window.exportWebM === 'function'")

        with page.expect_download() as download_info:
            page.evaluate("window.exportWebM()")
        download = download_info.value

        saved = self.save_download(download, "abc-transist.webm")
        verify_webm(saved)


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()

