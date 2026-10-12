import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from images import validate_images, model_input, anthropic_content
from providers import user_turn
from chatgpt_provider import translate_input
from web_chat import WebChat
from claude_subscription import ClaudeSubscriptionProvider

# Real 1x1 PNG, no external network or private files involved.
DATA = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aG1sAAAAASUVORK5CYII='
IMAGE = {'name': 'pixel.png', 'data_url': 'data:image/png;base64,' + DATA}


class ImageTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_images([IMAGE]), [IMAGE])
        for bad in ([IMAGE] * 5, {}, [{'name': 'x', 'data_url': 'https://example.com/x.png'}],
                    [{'name': 'x', 'data_url': 'data:image/svg+xml;base64,abcd'}],
                    [{'name': 'x', 'data_url': 'data:image/png;base64,!!!!'}],
                    [{'name': 'x', 'data_url': 'data:image/png;base64,' + base64.b64encode(b'not an image').decode()}],
                    [{'name': 'x', 'data_url': 'data:image/png;base64,' + 'A' * 7000000}]):
            with self.assertRaises(ValueError):
                validate_images(bad)

    def test_model_adapters(self):
        turn = model_input('What is this?', [IMAGE])
        self.assertEqual(translate_input(turn), turn)
        blocks = user_turn(turn)['content']
        self.assertEqual(blocks[0], {'type': 'text', 'text': 'What is this?'})
        self.assertEqual(blocks[1]['source'], {'type': 'base64', 'media_type': 'image/png', 'data': DATA})
        self.assertEqual(model_input('Hello', []), 'Hello')
        self.assertEqual(len(anthropic_content(model_input('', [IMAGE])[0]['content'])), 1)

    def test_chat_persists_and_forwards_images_and_retains_validation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            chat = WebChat(directory)
            chat.ready = True
            chat.configuration['connected'] = True
            self.assertEqual(chat.submit({'text': '', 'images': [IMAGE]})[0], 202)
            self.assertEqual(chat.inbox.get_nowait()['images'], [IMAGE])
            self.assertEqual(chat.snapshot(0)['events'][-1]['images'], [IMAGE])
            chat.busy = False
            self.assertEqual(chat.submit({'text': '', 'images': [IMAGE] * 5})[0], 400)
            self.assertFalse(chat.busy)
            chat.approval_id = 'pending'
            self.assertEqual(chat.submit({'text': 'hello', 'images': [IMAGE]})[0], 409)

    def test_claude_cli_receives_image_blocks_and_replays_them(self):
        auth = SimpleNamespace(require_signed_in=lambda: None, command=lambda *args: ['claude', *args],
                               env={}, directory='.')
        provider = ClaudeSubscriptionProvider(auth=auth)
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'system', 'subtype': 'init'}) + '\n' + json.dumps({'type': 'result', 'structured_output': {'text': 'A pixel', 'commands': []}}))
        with patch('claude_subscription.subprocess.run', return_value=reply) as run:
            first = provider.create_response(instructions='Test', input=model_input('Describe', [IMAGE]))
            provider.create_response(instructions='Test', input='Again', previous_response_id=first.id)
        for call in run.call_args_list:
            self.assertIn('--input-format', call.args[0])
            stdin = json.loads(call.kwargs['input'])
            self.assertEqual(stdin['message']['content'][1]['source']['data'], DATA)
            self.assertNotIn(DATA, stdin['message']['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
