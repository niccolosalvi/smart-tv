import subprocess
import unittest
from unittest.mock import patch

from health import Health


class HealthTests(unittest.TestCase):
    def test_missing_systemd_is_unknown_not_enabled(self):
        with patch('health.subprocess.run', side_effect=FileNotFoundError):
            data = Health().snapshot()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['service']['available'])
        self.assertIsNone(data['service']['enabled'])

    def test_unit_status_and_cache(self):
        output = 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nNextElapseUSecRealtime=Sun 2026-10-25 11:00:00 CET\n'
        with patch('health.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            health = Health()
            first = health.snapshot(True)
            second = health.snapshot(False)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(first['service']['enabled'])
        self.assertTrue(first['refresh_timer']['available'])
        self.assertTrue(first['command_busy'])
        self.assertFalse(second['command_busy'])
        self.assertEqual(first['session'], second['session'])
        self.assertNotEqual(first['session'], Health().session)

    def test_timeout_does_not_break_health(self):
        with patch('health.subprocess.run', side_effect=subprocess.TimeoutExpired('systemctl', 2)):
            data = Health().snapshot()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['refresh_timer']['available'])

    def test_missing_unit_is_not_presented_as_installed(self):
        output = 'LoadState=not-found\nActiveState=inactive\nUnitFileState=\n'
        with patch('health.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)):
            data = Health().snapshot()
        self.assertFalse(data['service']['available'])
        self.assertFalse(data['service']['enabled'])
