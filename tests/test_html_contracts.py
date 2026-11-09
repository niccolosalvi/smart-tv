"""Check the DOM hooks consumed by the shared browser scripts."""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import unittest

WEB = Path(__file__).resolve().parents[1] / 'web'


class Page(HTMLParser):
    def __init__(self, name):
        super().__init__()
        self.ids = []
        self.scripts = []
        self.feed((WEB / name).read_text(encoding='utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'script' and 'src' in attrs:
            self.scripts.append(attrs['src'])


class HTMLContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed for frontend execution')
    def test_frontend_behavior(self):
        result = subprocess.run(
            ['node', str(Path(__file__).with_name('frontend_contracts.cjs'))],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_page_ids_are_unique(self):
        for name in ('index.html', 'share.html', 'screen.html'):
            with self.subTest(page=name):
                ids = Page(name).ids
                self.assertEqual(len(ids), len(set(ids)))

    def test_capture_page_supplies_shared_script_controls(self):
        script = (WEB / 'share.js').read_text(encoding='utf-8')
        required = set(re.findall(r"\$\('([^']+)'\)", script))
        page = Page('share.html')
        self.assertFalse(required - set(page.ids))
        self.assertIn('share-config', page.ids)
        self.assertIn('/assets/audio-share.js', page.scripts)
        self.assertIn('/assets/share.js', page.scripts)

    def test_remote_keeps_capture_on_dedicated_page(self):
        page = Page('index.html')
        self.assertNotIn('/assets/share.js', page.scripts)
        self.assertIn('/assets/keyboard.js', page.scripts)
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('href="__SHARE_URL__"', html)
        self.assertIn('data-screen="open" data-source="screen"', html)

    def test_remote_script_dom_hooks_exist(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        script = re.search(r'<script nonce="__CSRF__">(.*?)</script>', html, re.S)[1]
        required = set(re.findall(r"querySelector\('#([\w-]+)'\)", script))
        self.assertFalse(required - set(Page('index.html').ids))

    def test_catalogue_has_45_distinct_channels_and_six_modes(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        catalogue = re.search(r'const allChannels=\[(.*?)\n\];', html, re.S)[1]
        numbers = re.findall(r"\['(\d+)'", catalogue)
        self.assertEqual(len(numbers), 45)
        self.assertEqual(len(set(numbers)), 45)
        navigation = re.search(r'<nav class="sources".*?</nav>', html, re.S)[0]
        self.assertEqual(re.findall(r'data-source="([^"]+)"', navigation),
                         ['tv', 'youtube', 'netflix', 'primevideo', 'pc', 'screen'])

    def test_receiver_supplies_script_controls(self):
        script = (WEB / 'screen.js').read_text(encoding='utf-8')
        required = set(re.findall(r"getElementById\('([^']+)'\)", script))
        self.assertFalse(required - set(Page('screen.html').ids))


if __name__ == '__main__':
    unittest.main()
