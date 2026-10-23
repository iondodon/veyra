import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from unittest.mock import patch
from web_chat import WebChat
from video_share import resolve, byte_range, browse, watch


class VideoTests(unittest.TestCase):
    def test_paths_and_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'link').symlink_to('/tmp')
            for path in ('/videos/../other', '/videos/%2e%2e/other', '/videos/.hidden', '/videos/link/file', '/videos/a%5cb'):
                with self.assertRaises(ValueError):
                    resolve(root, path)
        self.assertEqual(byte_range('bytes=2-4', 10), (2, 4))
        self.assertEqual(byte_range('bytes=-3', 10), (7, 9))
        self.assertEqual(byte_range('bytes=3-', 10), (3, 9))
        for value in ('bytes=30-', 'bytes=4-2', 'bytes=-0', 'bytes=0-1,3-4'):
            with self.assertRaises(ValueError):
                byte_range(value, 10)

    def test_library_and_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('Lesson 10.mp4', 'Lesson 2.mp4', 'Lesson 1.mp4',
                         '<unsafe>.mp4', '.private.mp4', 'script.html'):
                (root / name).write_bytes(b'video')
            (root / 'outside.mp4').symlink_to('/tmp/elsewhere')
            body = browse(root, root).decode()
            self.assertLess(body.index('Lesson 2.mp4'), body.index('Lesson 10.mp4'))
            self.assertNotIn('script.html', body)
            self.assertNotIn('.private.mp4', body)
            self.assertNotIn('outside.mp4', body)
            self.assertIn('&lt;unsafe&gt;.mp4', body)
            player = watch(root, root / 'Lesson 2.mp4').decode()
            self.assertIn('Lesson%201.mp4?watch=1', player)
            self.assertIn('Lesson%2010.mp4?watch=1', player)
            self.assertNotIn('autoplay', player)

    def test_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'media'; root.mkdir()
            (root / 'test.mp4').write_bytes(b'0123456789')
            (root / 'unsafe.html').write_text('<script>bad</script>')
            chat = WebChat(tmp)
            with patch('video_share.ROOT', root):
                port = chat.start(0)
                base = f'http://localhost:{port}'
                try:
                    with urlopen(base + '/videos/') as response:
                        body = response.read()
                        self.assertIn(b'test.mp4', body)
                        self.assertIn(b'/app-updates.js', body)
                    with urlopen(base + '/videos/test.mp4?watch=1') as response:
                        player = response.read()
                        self.assertIn(b'<video controls playsinline', player)
                        self.assertIn(b'src="/videos/test.mp4"', player)
                        self.assertIn(b'/app-updates.js', player)
                        self.assertIn(b'/videos.css', player)
                    with urlopen(base + '/videos.css') as response:
                        self.assertIn('text/css', response.headers['Content-Type'])
                        self.assertIn(b'video', response.read())
                    with urlopen(Request(base + '/videos/test.mp4', headers={'Range': 'bytes=2-4'})) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.read(), b'234')
                        self.assertEqual(response.headers['Content-Range'], 'bytes 2-4/10')
                    with urlopen(Request(base + '/videos/test.mp4', method='HEAD')) as response:
                        self.assertEqual(response.read(), b'')
                        self.assertEqual(response.headers['Content-Length'], '10')
                    for path, status in (('/videos/unsafe.html', 403), ('/videos/%2e%2e/test.mp4', 403), ('/videos/missing.mp4', 404)):
                        with self.assertRaises(HTTPError) as caught:
                            urlopen(base + path)
                        self.assertEqual(caught.exception.code, status)
                        caught.exception.close()
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(base + '/videos/', headers={'Host': f'8.8.8.8:{port}'}))
                    self.assertEqual(caught.exception.code, 401)
                    caught.exception.close()
                finally:
                    chat.close()
