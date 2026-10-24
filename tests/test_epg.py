import json
import tempfile
import unittest
from pathlib import Path
from epg import Guide, FAVORITES, SOURCE, Feed, parse_guide, parse_time


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'guide.json'
        self.now = parse_time('20261023090000 +0000')
        self.data = {n: [{'start': self.now - 60, 'end': self.now + 60, 'title': name},
                         {'start': self.now + 60, 'end': self.now + 3600, 'title': 'Successivo'}]
                     for n, name, _ in FAVORITES}
        self.guide = Guide(self.path, fetch=lambda: self.data, clock=lambda: self.now)

    def test_time_offsets_and_dst_repeated_hour(self):
        self.assertEqual(parse_time('20261023110000 +0200'), self.now)
        self.assertEqual(parse_time('20261025023000 +0100') -
                         parse_time('20261025023000 +0200'), 3600)

    def test_xml_mapping_ignores_unrelated_and_invalid_programs(self):
        programs = parse_guide(b'''<tv>
          <programme channel="Canale 5.it" start="20261023090000 +0000" stop="20261023100000 +0000"><title>A &amp; B</title></programme>
          <programme channel="unrelated"><title>Other</title></programme>
          <programme channel="Canale 5.it" start="bad"><title>Invalid</title></programme>
        </tv>''')
        self.assertEqual(programs['5'][0]['title'], 'A & B')
        self.assertEqual(sum(map(len, programs.values())), 1)

    def test_exact_boundary_switches_program_without_download(self):
        self.assertTrue(self.guide.update())
        self.assertEqual(self.guide.snapshot()['channels'][0]['current']['title'], 'Canale 5')
        self.now += 60
        self.assertEqual(self.guide.snapshot()['channels'][0]['current']['title'], 'Successivo')
        self.now += 3540
        self.assertIsNone(self.guide.snapshot()['channels'][0]['current'])

    def test_failed_refresh_retains_disk_and_memory_cache(self):
        self.guide.update()
        saved = self.path.read_bytes()
        self.guide.fetch = lambda: (_ for _ in ()).throw(TimeoutError())
        self.assertFalse(self.guide.update())
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(self.guide.snapshot()['error'], 'TimeoutError')
        self.assertIsNotNone(self.guide.snapshot()['channels'][0]['current'])

    def test_incomplete_or_expired_feed_does_not_replace_good_cache(self):
        self.guide.update()
        self.data['52'] = []
        self.assertFalse(self.guide.update())
        self.assertIsNotNone(self.guide.snapshot()['channels'][-1]['current'])
        self.now += 86400 * 3
        self.assertFalse(self.guide.update())
        self.assertTrue(self.guide.snapshot()['stale'])
        self.assertIsNone(self.guide.snapshot()['channels'][0]['current'])

    def test_cache_survives_restart_and_corrupt_cache_is_ignored(self):
        self.guide.update()
        loaded = Guide(self.path, clock=lambda: self.now)
        self.assertEqual(loaded.snapshot(), self.guide.snapshot())
        self.path.write_text('{bad')
        self.assertIsNone(Guide(self.path).snapshot()['updated_at'])

    def test_reject_xml_declarations(self):
        with self.assertRaises(ValueError):
            parse_guide(b'<!DOCTYPE tv><tv/>')

    def test_italy8_identifiers_for_all_six_channels(self):
        identifiers = ['Canale 5.it', 'Italia Uno.it', 'TV8.it', 'NOVE.it', 'canale 20.it', 'DMAX.it']
        xml = '<tv>' + ''.join(
            '<programme channel="'+identifier+'" start="20261024090000 +0000" stop="20261024100000 +0000"><title>Today '+identifier+'</title></programme>'
            for identifier in identifiers) + '</tv>'
        parsed = parse_guide(xml.encode())
        self.assertEqual(list(parsed), ['5', '6', '8', '9', '20', '52'])
        self.assertTrue(all(len(items) == 1 for items in parsed.values()))

    def test_old_source_cache_cannot_reappear_after_migration(self):
        self.guide.update()
        saved = json.loads(self.path.read_text())
        saved['source'] = 'https://www.open-epg.com/files/italy5.xml.gz'
        self.path.write_text(json.dumps(saved))
        loaded = Guide(self.path, clock=lambda: self.now)
        self.assertIsNone(loaded.snapshot()['updated_at'])
        self.assertTrue(all(c['current'] is None and c['next'] is None for c in loaded.snapshot()['channels']))
        self.assertTrue(SOURCE.endswith('/italy8.xml.gz'))

    def test_download_time_does_not_hide_old_source_file(self):
        self.guide.fetch = lambda: Feed(self.data, self.now - 3*86400)
        self.assertTrue(self.guide.update())
        snapshot = self.guide.snapshot()
        self.assertEqual(snapshot['downloaded_at'], self.now)
        self.assertEqual(snapshot['source_updated_at'], self.now - 3*86400)
        self.assertTrue(snapshot['stale'])
        self.assertTrue(Guide(self.path, clock=lambda: self.now).snapshot()['stale'])

    def test_new_favorite_refreshes_cache_without_losing_other_channels(self):
        self.guide.update()
        saved = json.loads(self.path.read_text())
        del saved['programs']['9']
        self.path.write_text(json.dumps(saved))
        loaded = Guide(self.path, fetch=lambda: self.data, clock=lambda: self.now)
        self.assertIsNone(loaded.snapshot()['updated_at'])
        self.assertIsNotNone(loaded.snapshot()['channels'][0]['current'])
        self.assertTrue(loaded.update())
        nove = next(c for c in loaded.snapshot()['channels'] if c['number'] == '9')
        self.assertEqual(nove['current']['title'], 'NOVE')
        self.assertEqual(nove['next']['start'], self.now + 60)

    def scheduled_worker(self, waits=2):
        """Advance the worker's wall clock instead of waiting real days."""
        guide = self.guide
        clock_owner = self
        class Stop:
            stopped = False
            delays = []
            def is_set(self):
                return self.stopped
            def wait(self, seconds):
                self.delays.append(seconds)
                clock_owner.now += seconds
                self.stopped = len(self.delays) >= waits
                return self.stopped
        guide.stop = Stop()
        guide.start()
        guide.worker.join(timeout=2)
        self.assertFalse(guide.worker.is_alive())
        return guide.stop.delays

    def future_programs(self):
        return {n: [{'start': self.now - 1, 'end': self.now + 2*86400, 'title': name}]
                for n, name, _ in FAVORITES}

    def test_daily_worker_refreshes_without_browser_or_manual_call(self):
        self.guide.update()
        initial = self.now
        attempts = []
        def fetch():
            attempts.append(self.now)
            return self.future_programs()
        self.guide.fetch = fetch
        self.assertEqual(self.scheduled_worker(), [86400, 86400])
        self.assertEqual(attempts, [initial + 86400])

    def test_worker_retries_after_failure_without_user_action(self):
        initial = self.now
        attempts = []
        def fetch():
            attempts.append(self.now)
            if len(attempts) == 1:
                raise TimeoutError()
            return self.future_programs()
        self.guide.fetch = fetch
        self.assertEqual(self.scheduled_worker(), [21600, 86400])
        self.assertEqual(attempts, [initial, initial + 21600])
        self.assertIsNone(self.guide.error)

    def test_restart_keeps_daily_due_time_and_catches_up_if_overdue(self):
        self.guide.update()
        due = self.now + 86400
        self.now += 3600
        restarted = Guide(self.path, clock=lambda: self.now)
        self.assertEqual(restarted.snapshot()['next_refresh_at'], due)
        self.now = due + 60
        self.guide = Guide(self.path, fetch=self.future_programs, clock=lambda: self.now)
        self.assertEqual(self.scheduled_worker(waits=1), [86400])
        self.assertEqual(self.guide.updated, due + 60)


if __name__ == '__main__':
    unittest.main()
