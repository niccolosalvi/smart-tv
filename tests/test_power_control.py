import threading
import unittest
from unittest.mock import Mock, patch, call
from power_control import magic_packet, normalize_mac, power_off
from remote import Remote

class PowerTests(unittest.TestCase):
    def test_magic_packet_contains_exact_target_repetitions(self):
        mac = bytes.fromhex('001122aabbcc')
        self.assertEqual(magic_packet('00:11:22:AA:BB:CC'), b'\xff'*6 + mac*16)
        self.assertEqual(len(magic_packet('001122aabbcc')), 102)

    def test_invalid_mac_cannot_generate_packet(self):
        for value in ('000000000000', None, 'bad', '00:11:22:33:44:ZZ'):
            with self.assertRaises(ValueError):
                normalize_mac(value)

    @patch('power_control.prepare_wake')
    @patch('power_control.time.sleep')
    @patch('power_control.record', return_value={'status': 'sent'})
    def test_off_uses_one_short_press_and_release(self, record, sleep, prepare):
        remote = Remote.__new__(Remote)
        remote.lock = threading.Lock()
        remote.tv = Mock()
        remote.tv.is_alive.return_value = True
        power_off(remote)
        self.assertEqual(remote.tv.send_key.call_args_list, [
            call('KEY_POWER', cmd='Press', key_press_delay=0.2),
            call('KEY_POWER', cmd='Release', key_press_delay=0.2)])
        remote.tv.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
