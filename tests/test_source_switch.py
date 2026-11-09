import threading
import unittest
from unittest.mock import Mock, patch, call
from remote import Remote, volume_keys

class SourceSwitchTests(unittest.TestCase):
    def remote(self):
        instance = Remote.__new__(Remote)
        instance.lock = threading.Lock()
        instance.tv = Mock()
        instance.tv.is_alive.return_value = True
        return instance

    @patch('remote.record', return_value={'status': 'sent'})
    def test_exit_precedes_tv_with_two_second_delay(self, record):
        instance = self.remote()
        instance.switch_to_tv()
        self.assertEqual(instance.tv.send_key.call_args_list, [
            call('KEY_EXIT', key_press_delay=2.0),
            call('KEY_TV', key_press_delay=0.35),
            call('KEY_EXIT', key_press_delay=0.35)])

    @patch('remote.record')
    def test_failed_sequence_stops_without_retry(self, record):
        instance = self.remote()
        instance.tv.send_key.side_effect = [None, OSError('socket closed')]
        with self.assertRaises(RuntimeError):
            instance.switch_to_tv()
        self.assertEqual(instance.tv.send_key.call_count, 2)
        instance.tv.close.assert_called_once()

    @patch('remote.record', return_value={'status': 'sent'})
    def test_pc_selects_hdmi1_after_exiting_app(self, record):
        instance = self.remote()
        instance.switch_to_pc()
        self.assertEqual(instance.tv.send_key.call_args_list, [
            call('KEY_EXIT', key_press_delay=2.0),
            call('KEY_HDMI1', key_press_delay=0.35),
            call('KEY_EXIT', key_press_delay=0.35)])

    @patch('remote.record', return_value={'status': 'sent'})
    def test_volume_request_sends_two_and_only_two_clicks(self, record):
        for step, key in [(2, 'KEY_VOLUP'), (-2, 'KEY_VOLDOWN')]:
            instance = self.remote()
            instance.send(volume_keys(step))
            self.assertEqual(instance.tv.send_key.call_args_list,
                             [call(key, key_press_delay=None)] * 2)

    def test_volume_rejects_unbounded_repeats_and_coercion(self):
        for value in [True, False, 1, -1, 0, 100, '2', 2.0, [], None]:
            with self.assertRaises(ValueError):
                volume_keys(value)

    @patch('remote.record', return_value={'status': 'sent'})
    def test_open_screen_uses_native_browser_url_without_navigation_keys(self, record):
        instance = self.remote()
        instance.open_screen('http://198.51.100.20:8765/screen')
        instance.tv.open_browser.assert_called_once_with('http://198.51.100.20:8765/screen')
        instance.tv.send_key.assert_not_called()

    @patch('remote.record',return_value={'status':'sent'})
    def test_connected_receiver_reuses_browser_without_new_url_tab(self,record):
        instance=self.remote();instance.open_screen('http://198.51.100.20:8765/screen',reuse=True)
        instance.tv.run_app.assert_called_once_with('org.tizen.browser','NATIVE_LAUNCH','')
        instance.tv.open_browser.assert_not_called()

    @patch('remote.record')
    def test_browser_failure_closes_connection_without_retry(self, record):
        instance = self.remote()
        instance.tv.open_browser.side_effect = OSError('connection closed')
        with self.assertRaises(RuntimeError):
            instance.open_screen('http://198.51.100.20:8765/screen')
        instance.tv.open_browser.assert_called_once()
        instance.tv.close.assert_called_once()

    def test_browser_refuses_loopback_and_arbitrary_page_urls(self):
        instance = self.remote()
        for url in ['http://127.0.0.1:8765/screen', 'http://198.51.100.20:8765/other',
                    'http://user:pass@198.51.100.20:8765/screen',
                    'http://198.51.100.20:8765/screen?url=other', 'javascript:alert(1)']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                instance.open_screen(url)
        instance.tv.open_browser.assert_not_called()

    @patch('remote.record',return_value={'status':'sent'})
    @patch('remote.time.sleep')
    def test_stop_forces_browser_exit_with_balanced_long_return_press(self,sleep,record):
        instance=self.remote();instance.close_screen()
        self.assertEqual(instance.tv.send_key.call_args_list,[
            call('KEY_RETURN',cmd='Press',key_press_delay=0),
            call('KEY_RETURN',cmd='Release',key_press_delay=0)])
        sleep.assert_called_once_with(2)

if __name__ == '__main__':
    unittest.main()
