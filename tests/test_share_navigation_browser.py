"""Optional real-browser regression: collapsible settings and return to TV antenna.

Run with SMART_TV_BROWSER_TESTS=1 and Playwright/Edge installed.
"""
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch


@unittest.skipUnless(os.environ.get('SMART_TV_BROWSER_TESTS')=='1', 'Optional Playwright/Edge test')
class ShareNavigationBrowserTests(unittest.TestCase):
    def test_collapsible_sections_removed_open_tv_and_back_switches_to_antenna(self):
        from playwright.sync_api import sync_playwright, expect
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ,{'LOCALAPPDATA':runtime,'TV_HOST':'127.0.0.1'}):
            import server
            from view_state import ViewState
            from pathlib import Path
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);http.daemon_threads=True
            relay=server.ShareRelay(server.CSRF,http_port=http.server_port)
            relay.start('127.0.0.1',0)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{http.server_port}'
            remote=Mock();remote.tv.is_alive.return_value=True
            remote.send.return_value={'status':'sent'};remote.close_screen.return_value={'status':'sent'}
            remote.switch_to_tv.return_value={'status':'sent'}
            try:
                with patch.object(server,'sharing',relay),patch.object(server,'remote',remote), \
                        patch.object(server,'view',ViewState(Path(runtime)/'view.json')),sync_playwright() as pw:
                    browser=pw.chromium.launch(channel='msedge',headless=True)
                    receiver=browser.new_page();receiver.goto(base+'/screen')
                    expect(receiver.locator('#status')).to_contain_text('TV collegata')
                    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                    sender=context.new_page();errors=[]
                    sender.on('pageerror',lambda error:errors.append(str(error)))
                    sender.add_init_script('''
                        Object.defineProperty(window,'MediaStreamTrackProcessor',{value:undefined});
                        window.captureCalls=0;
                        navigator.mediaDevices.getDisplayMedia=async()=>{
                            window.captureCalls++;
                            const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;
                            const ctx=canvas.getContext('2d');let tick=0;
                            setInterval(()=>{ctx.fillStyle='rgb('+(++tick%255)+',90,140)';ctx.fillRect(0,0,640,360);},60);
                            const stream=canvas.captureStream(15);window.captureTrack=stream.getVideoTracks()[0];return stream;
                        };
                    ''')
                    sender.goto(base+'/')
                    sender.locator('button[data-screen="open"]').click()
                    expect(sender).to_have_url(base+'/share',timeout=10000)
                    remote.open_screen.assert_not_called()
                    expect(sender.locator('#open-tv')).to_have_count(0)
                    expect(sender.locator('#preview-panel')).to_have_count(0)
                    sender.locator('button[data-volume="2"]').click()
                    deadline=time.monotonic()+4
                    while remote.send.call_count<1 and time.monotonic()<deadline:sender.wait_for_timeout(50)
                    remote.send.assert_called_once_with(['KEY_VOLUP','KEY_VOLUP'],'volume:2')
                    video_sect=sender.locator('#video-section')
                    audio_sect=sender.locator('#audio-section')
                    self.assertIsNone(video_sect.get_attribute('open'))
                    self.assertIsNone(audio_sect.get_attribute('open'))
                    expect(sender.locator('#transport')).to_be_hidden()
                    expect(sender.locator('#audio-enabled')).to_be_hidden()

                    video_sect.locator('summary.collapsible-head').click()
                    audio_sect.locator('summary.collapsible-head').click()
                    expect(sender.locator('#transport')).to_be_visible()
                    expect(sender.locator('#audio-enabled')).to_be_visible()

                    sender.locator('#audio-enabled').uncheck()
                    sender.locator('#transport').select_option('jpeg')
                    video_sect.locator('summary.collapsible-head').click()
                    audio_sect.locator('summary.collapsible-head').click()
                    expect(sender.locator('#transport')).to_be_hidden()
                    expect(sender.locator('#audio-enabled')).to_be_hidden()

                    sender.locator('#start').click()
                    expect(sender.locator('#status')).to_contain_text('ricevute',timeout=12000)
                    deadline=time.monotonic()+6
                    while relay.metrics['packets']<3 and time.monotonic()<deadline:sender.wait_for_timeout(100)
                    self.assertGreaterEqual(relay.metrics['packets'],3)

                    sender.locator('#back').click()
                    expect(sender).to_have_url(base+'/',timeout=10000)
                    expect(sender.locator('#view-select')).to_have_value('tv')
                    expect(sender.locator('#channels-panel')).to_be_visible()
                    remote.switch_to_tv.assert_called_once_with()
                    remote.close_screen.assert_not_called()
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                relay.stop();http.shutdown();http.server_close();thread.join()
