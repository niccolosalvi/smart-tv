"""YouTube search validation and DIAL transport, with no real TV effects."""
import json
import unittest
from unittest.mock import Mock, MagicMock, patch
from urllib.parse import parse_qs

from diagnostics import Timeline
from remote import Remote
from text_input import extract_youtube_video_id, validate_query, youtube_search_payload, youtube_video_payload


class SearchInputTests(unittest.TestCase):
    def test_encoding_preserves_spaces_unicode_and_reserved_characters(self):
        query = 'Caffè + tè & musica 🎵 #live? q=x'
        fields = parse_qs(youtube_search_payload(query).decode())
        self.assertEqual(fields, {'launch': ['search'], 'q': [query]})

    def test_invalid_input_is_rejected_without_coercion(self):
        for value in [None, [], {}, 1, True, '', '  ', 'a' * 129,
                      'a\nb', 'a\x00b', 'a\u2028b', 'a\u200eb', 'a\ud800b']:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                validate_query(value)

    def test_extracts_youtube_video_id_from_supported_url_formats(self):
        for url in [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&t=42s',
            'https://m.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://music.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ?si=test123',
            'youtu.be/dQw4w9WgXcQ',
            'youtube.com/watch?v=dQw4w9WgXcQ',
            'https://www.youtube.com/shorts/dQw4w9WgXcQ',
            'https://www.youtube.com/live/dQw4w9WgXcQ?feature=share',
            'https://www.youtube.com/embed/dQw4w9WgXcQ',
        ]:
            with self.subTest(url=url):
                self.assertEqual(extract_youtube_video_id(url), 'dQw4w9WgXcQ')
                self.assertEqual(parse_qs(youtube_video_payload(url).decode()), {'v': ['dQw4w9WgXcQ']})

    def test_rejects_non_youtube_or_malformed_video_links(self):
        for bad in [
            None, 123, '', '   ', 'dQw4w9WgXcQ',
            'https://evil.example/watch?v=dQw4w9WgXcQ',
            'https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ',
            'https://user:pass@www.youtube.com/watch?v=dQw4w9WgXcQ',
            'ftp://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://www.youtube.com/watch?v=too_short',
            'https://www.youtube.com/watch?v=too_long_12345',
            'https://www.youtube.com/results?search_query=music',
            'https://youtu.be/dQw4w9WgXcQ\n',
        ]:
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                extract_youtube_video_id(bad)


class SearchTransportTests(unittest.TestCase):
    def setUp(self):
        self.record_patch = patch('remote.record', side_effect=lambda action, start, status, **extra:
                                  {'status': status, **extra})
        self.record = self.record_patch.start()
        self.addCleanup(self.record_patch.stop)
        self.log = Timeline()
        p = patch('remote.timeline', self.log)
        p.start()
        self.addCleanup(p.stop)
        self.remote = Remote()
        self.remote.tv = Mock()
        self.opener = MagicMock()
        self.opener.open.return_value.__enter__.return_value.status = 201
        p = patch('remote.urllib.request.build_opener', return_value=self.opener)
        p.start()
        self.addCleanup(p.stop)

    def test_search_uses_dial_origin_and_does_not_connect_ws_or_navigate_grid(self):
        query = 'Caffè & musica'
        result = self.remote.search_youtube(query)
        self.assertEqual((result['status'], result['transport'], result['http_status']),
                         ('sent', 'dial', 201))
        request = self.opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith(':8080/ws/apps/YouTube'))
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Origin'), 'https://www.youtube.com')
        self.assertEqual(request.get_header('Content-type'), 'application/x-www-form-urlencoded')
        self.assertEqual(parse_qs(request.data.decode()), {'launch': ['search'], 'q': [query]})
        self.remote.tv.open.assert_not_called()
        self.remote.tv.send_key.assert_not_called()
        self.remote.tv.run_app.assert_not_called()
        self.assertNotIn(query, json.dumps(self.log.read()) + str(self.record.call_args_list))

    def test_failure_is_sanitized_and_not_retried(self):
        self.opener.open.side_effect = OSError('private query')
        with self.assertRaisesRegex(RuntimeError, '^OSError$'):
            self.remote.search_youtube('private query')
        self.opener.open.assert_called_once()
        self.assertNotIn('private query', str(self.record.call_args_list))
        self.remote.tv.close.assert_not_called()

    def test_invalid_query_never_opens_transport(self):
        with self.assertRaises(ValueError):
            self.remote.search_youtube('')
        self.opener.open.assert_not_called()

    def test_play_youtube_url_opens_app_presses_ok_on_profile_and_launches_video(self):
        self.remote.tv.is_alive.return_value = True
        url = 'https://youtu.be/dQw4w9WgXcQ?si=secretToken'
        result = self.remote.play_youtube_url(url, launch_delay=0)
        self.assertEqual((result['status'], result['transport'], result['http_status']),
                         ('sent', 'rest+ws+dial', 201))
        self.assertTrue(result['profile_confirmed'])
        calls = [c.args[0] for c in self.opener.open.call_args_list if hasattr(c.args[0], 'full_url')]
        post_calls = [req for req in calls if req.get_method() == 'POST']
        self.assertEqual(len(post_calls), 2)
        self.assertTrue(post_calls[0].full_url.endswith(':8001/api/v2/applications/111299001912'))
        self.assertTrue(post_calls[1].full_url.endswith(':8080/ws/apps/YouTube'))
        self.assertEqual(post_calls[1].get_header('Origin'), 'https://www.youtube.com')
        self.assertEqual(parse_qs(post_calls[1].data.decode()), {'v': ['dQw4w9WgXcQ']})
        self.remote.tv.send_key.assert_called_once_with('KEY_ENTER')
        self.assertNotIn('dQw4w9WgXcQ', json.dumps(self.log.read()) + str(self.record.call_args_list))
        self.assertNotIn('secretToken', json.dumps(self.log.read()) + str(self.record.call_args_list))

    def test_play_youtube_url_skips_ok_when_profile_already_active_and_playing(self):
        self.remote.tv.is_alive.return_value = True
        self.remote._yt_confirmed_psid = 'psid-active-1'
        self.remote._read_youtube_dial = Mock(return_value={
            'state': 'running', 'oisi': 'null', 'ts': '123',
            'psid': 'psid-active-1', 'sid': 'screen-1',
        })
        self.remote._youtube_lounge_state = Mock(side_effect=[
            {'video_id': 'prevVideo11', 'first_video_id': 'prevVideo11'},
            {'video_id': 'dQw4w9WgXcQ', 'first_video_id': 'prevVideo11'},
        ])
        with patch('remote.json.load', return_value={'running': True, 'visible': True}):
            result = self.remote.play_youtube_url('https://youtu.be/dQw4w9WgXcQ', launch_delay=0)
        self.assertFalse(result['profile_confirmed'])
        self.remote.tv.send_key.assert_not_called()

    def test_play_youtube_url_detects_profile_screen_after_dial_and_confirms_with_ok(self):
        self.remote.tv.is_alive.return_value = True
        self.remote._yt_confirmed_psid = 'psid-active-1'
        self.remote._read_youtube_dial = Mock(return_value={
            'state': 'running', 'oisi': 'null', 'ts': '123',
            'psid': 'psid-active-1', 'sid': 'screen-1',
        })
        self.remote._youtube_lounge_state = Mock(side_effect=[
            {'video_id': 'prevVideo11', 'first_video_id': 'prevVideo11'},
            {'video_id': None, 'first_video_id': 'dQw4w9WgXcQ'},
        ])
        with patch('remote.json.load', return_value={'running': True, 'visible': True}):
            result = self.remote.play_youtube_url('https://youtu.be/dQw4w9WgXcQ', launch_delay=0)
        self.assertTrue(result['profile_confirmed'])
        self.remote.tv.send_key.assert_called_once_with('KEY_ENTER')


if __name__ == '__main__':
    unittest.main()
