import tempfile
import unittest
from pathlib import Path
from view_state import ViewState

class ViewStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'context.json'
        self.view = ViewState(self.path)

    def test_source_changes_and_pause_preserves_view(self):
        self.view.after_command({'app': 'netflix'})
        self.assertEqual(self.view.after_command({'key': 'KEY_PAUSE'})['source'], 'netflix')
        state = self.view.after_command({'channel': '20'})
        self.assertEqual((state['source'], state['channel_requested'], state['basis']),
                         ('tv', '20', 'last_request'))

    def test_failed_switch_does_not_claim_target_source(self):
        self.view.select('tv')
        self.assertEqual(self.view.after_failure({'app': 'netflix'})['source'], 'unknown')

    def test_external_navigation_invalidates_context(self):
        for key in ('KEY_HOME', 'KEY_SOURCE', 'KEY_RETURN', 'KEY_EXIT'):
            self.view.select('netflix')
            self.assertEqual(self.view.after_command({'key': key})['source'], 'unknown')

    def test_restored_context_is_labelled_as_saved(self):
        self.view.after_command({'app': 'youtube'})
        restored = ViewState(self.path).get()
        self.assertEqual((restored['source'], restored['basis']), ('youtube', 'restored'))

    def test_corrupt_state_falls_back_to_unknown(self):
        self.path.write_text('null', encoding='utf-8')
        self.assertEqual(ViewState(self.path).get()['source'], 'unknown')

    def test_channel_up_does_not_invent_channel_number(self):
        self.view.after_command({'channel': '20'})
        self.assertIsNone(self.view.after_command({'key': 'KEY_CHUP'})['channel_requested'])

    def test_tv_request_changes_view(self):
        self.view.select('netflix')
        self.assertEqual(self.view.after_command({'key': 'KEY_TV'})['source'], 'tv')

    def test_pc_switch_and_volume_preserves_hdmi_context(self):
        self.assertEqual(self.view.after_command({'source': 'pc'})['source'], 'pc')
        self.assertEqual(self.view.after_command({'volume': 2})['source'], 'pc')
        self.assertEqual(self.view.after_failure({'source': 'pc'})['source'], 'unknown')

    def test_power_request_invalidates_source(self):
        self.view.select('netflix')
        self.assertEqual(self.view.after_command({'power': 'off'})['source'], 'unknown')

    def test_power_on_defaults_to_antenna_without_guessing_channel(self):
        self.view.select('netflix')
        state = self.view.after_command({'power': 'on'})
        self.assertEqual((state['source'], state['basis'], state['channel_requested']),
                         ('tv', 'power_on_default', None))
        self.assertEqual(self.view.after_failure({'power': 'on'})['source'], 'unknown')

if __name__ == '__main__':
    unittest.main()
