"""YouTube search validation and DIAL transport, with no real TV effects."""
import json
import unittest
from unittest.mock import Mock, MagicMock, patch
from urllib.parse import parse_qs

from diagnostics import Timeline
from remote import Remote
from text_input import validate_query, youtube_search_payload


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


if __name__ == '__main__':
    unittest.main()
