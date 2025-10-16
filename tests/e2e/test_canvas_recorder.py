import unittest

from .base import ExportTestCaseMixin, REPO_ROOT


class CanvasRecorderTests(ExportTestCaseMixin, unittest.TestCase):
    def stub_media_devices(self, page) -> None:
        page.add_init_script(
            """
            (() => {
              const stub = () => { throw new Error('media devices blocked'); };
              if (!navigator.mediaDevices) {
                Object.defineProperty(navigator, 'mediaDevices', {
                  configurable: true,
                  value: {}
                });
              }
              for (const name of ['getDisplayMedia', 'getUserMedia']) {
                Object.defineProperty(navigator.mediaDevices, name, {
                  configurable: true,
                  get() { return stub; }
                });
              }
            })();
            """
        )

    def test_overlay_uses_canvas_capture(self) -> None:
        page = self.make_page()
        self.stub_media_devices(page)
        self.goto_html(page, 'overlay.html?autostart=0')
        page.fill('#title', 'Test title')
        page.fill('#subtitle', 'Sub')
        page.fill('#body', 'Line one\nLine two')
        page.select_option('#holdSec', '0')
        with page.expect_download(timeout=120000) as dl_info:
            page.evaluate('window.exportWebM && window.exportWebM()')
        download = dl_info.value
        saved = self.save_download(download, 'overlay-test.webm')
        self.assertTrue(saved.exists())

    def test_chat_uses_canvas_capture(self) -> None:
        page = self.make_page()
        self.stub_media_devices(page)
        self.goto_html(page, 'chat-typing.html?autostart=0')
        page.fill('#prompt', 'Q?')
        page.fill('#answer', 'A!')
        page.fill('#thinkSec', '0')
        page.fill('#cpsPrompt', '60')
        page.fill('#cpsAnswer', '60')
        page.fill('#pauseSentence', '0')
        page.fill('#pauseComma', '0')
        with page.expect_download(timeout=120000) as dl_info:
            page.evaluate('window.exportWebM && window.exportWebM()')
        download = dl_info.value
        saved = self.save_download(download, 'chat-test.webm')
        self.assertTrue(saved.exists())

    def test_abc_uses_canvas_capture(self) -> None:
        page = self.make_page()
        self.stub_media_devices(page)
        self.goto_html(page, 'abc-transist.html?autostart=0')
        samples = REPO_ROOT / 'samples'
        page.set_input_files('#fA', str((samples / '1_AgentKit-ot-OpenAI.png').resolve()))
        page.set_input_files('#fB', str((samples / '2_Prostaya-formulirovka-zadachi.png').resolve()))
        page.set_input_files('#fC', str((samples / '3_Klyuchevye-preimushestva-AgentKit.png').resolve()))
        page.fill('#durA', '2')
        page.fill('#durB', '2')
        page.fill('#durC', '2')
        with page.expect_download(timeout=180000) as dl_info:
            page.evaluate('window.exportWebM && window.exportWebM()')
        download = dl_info.value
        saved = self.save_download(download, 'abc-test.webm')
        self.assertTrue(saved.exists())
