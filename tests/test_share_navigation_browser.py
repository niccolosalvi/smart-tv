"""Optional real-browser regression: one capture survives mobile remote navigation.

Run with SMART_TV_BROWSER_TESTS=1 and Playwright/Edge installed. This simulates
the video-render callback fallback, not iOS background execution.
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
    def test_mobile_remote_navigation_keeps_same_capture_and_stop_releases_it(self):
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
                    sender.goto(base+'/share');sender.locator('#audio-enabled').uncheck()
                    sender.locator('#transport').select_option('jpeg');sender.locator('#start').click()
                    expect(sender.locator('#status')).to_contain_text('ricevute',timeout=12000)
                    original=relay.sender
                    def assert_more_frames():
                        before=relay.metrics['packets'];deadline=time.monotonic()+6
                        while relay.metrics['packets']<before+3 and time.monotonic()<deadline:sender.wait_for_timeout(100)
                        self.assertGreaterEqual(relay.metrics['packets'],before+3)
                        self.assertIs(relay.sender,original)
                        self.assertEqual(sender.evaluate('captureTrack.readyState'),'live')
                        self.assertEqual(sender.evaluate('captureCalls'),1)
                    sender.locator('#back').click()
                    expect(sender.locator('#remote-view')).to_be_visible()
                    panel=sender.frame_locator('#remote-frame')
                    expect(panel.locator('#view-select')).to_be_visible()
                    # A real remote button invokes the existing authenticated endpoint.
                    panel.locator('button[data-volume="2"]:visible').first.click()
                    expect(panel.locator('#status')).not_to_contain_text('Invio',timeout=5000)
                    self.assertEqual(remote.send.call_count,1)
                    assert_more_frames()
                    # Browser Back restores sharing inside the same capture document.
                    sender.go_back();expect(sender.locator('#share-view')).to_be_visible();assert_more_frames()
                    sender.locator('#back').click()
                    panel.locator('.share-link-btn').click()
                    expect(sender.locator('#share-view')).to_be_visible();assert_more_frames()
                    sender.locator('#back').click();sender.locator('#remote-stop').click()
                    expect(sender.locator('#remote-status')).to_have_text('Condivisione terminata.')
                    expect(sender.locator('#remote-stop')).to_be_disabled()
                    self.assertEqual(sender.evaluate('captureTrack.readyState'),'ended')
                    deadline=time.monotonic()+5
                    while remote.close_screen.call_count<1 and time.monotonic()<deadline:sender.wait_for_timeout(50)
                    remote.close_screen.assert_called_once()
                    self.assertFalse(errors,errors)
                    browser.close()
            finally:
                relay.stop();http.shutdown();http.server_close();thread.join()
