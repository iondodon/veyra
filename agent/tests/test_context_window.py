import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_chat import WebChat
from chatgpt_provider import translate_input
from claude_subscription import ClaudeSubscriptionProvider
from test_images import IMAGE, DATA


class ContextWindowTests(unittest.TestCase):
    def test_exact_window_order_restart_and_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            chat = WebChat(directory)
            for i in range(65):
                chat.publish(str(i), role='user' if i % 2 == 0 else 'assistant')
                chat.publish('tool noise', role='tool', kind='output')
                chat.publish('setup noise', role='assistant', kind='setup')
                chat.publish('approval noise', role='assistant', kind='approval')
            expected = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i)} for i in range(15,65)]
            self.assertEqual(chat.context_window(), expected)
            restarted = WebChat(directory)
            self.assertEqual(restarted.context_window(), expected)
            restarted.ready = True
            self.assertEqual(restarted.submit({'text': 'latest'})[0], 202)
            context = restarted.inbox.get_nowait()['context']
            self.assertEqual(len(context), 50)
            self.assertEqual(context, expected[1:] + [{'role': 'user', 'content': 'latest'}])

    def test_empty_short_image_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            chat = WebChat(directory)
            self.assertEqual(chat.context_window(), [])
            chat.publish('Describe', role='user', images=[IMAGE])
            chat.publish('A pixel', role='assistant')
            context = chat.context_window()
            self.assertEqual(context[0]['content'][1]['image_url'], IMAGE['data_url'])
            self.assertEqual(translate_input(context), context)
            chat.credentials.save('openai', 'fixture-private-key')
            chat.publish('fixture-private-key', role='assistant')
            self.assertNotIn('fixture-private-key', str(chat.context_window()))

    def test_claude_window_roles_and_images(self):
        auth = SimpleNamespace(require_signed_in=lambda: None, command=lambda *args: ['claude', *args], env={}, directory='.')
        provider = ClaudeSubscriptionProvider(auth=auth)
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({'type': 'result', 'structured_output': {'text': 'Yes', 'commands': []}}))
        context = [{'role': 'assistant', 'content': 'Earlier reply'},
                   {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Describe'}, {'type': 'input_image', 'image_url': IMAGE['data_url']}]}]
        with patch('claude_subscription.subprocess.run', return_value=reply) as run:
            provider.create_response(instructions='Test', input=context)
        blocks = json.loads(run.call_args.kwargs['input'])['message']['content']
        turns = json.loads(blocks[0]['text'].split('\nImages below')[0])['conversation']
        self.assertEqual(turns[0], context[0])
        self.assertEqual(turns[1]['role'], 'user')
        self.assertEqual(blocks[1]['source']['data'], DATA)

    def test_anthropic_window_translation(self):
        from providers import AnthropicProvider
        provider = AnthropicProvider(api_key='test-key', model='claude-opus-5')
        context = [{'role': 'assistant', 'content': 'Earlier'},
                   {'role': 'user', 'content': [{'type': 'input_image', 'image_url': IMAGE['data_url']}]}]
        reply = SimpleNamespace(id='r1', stop_reason='end_turn', content=[])
        with patch.object(provider, '_call', return_value=reply) as call:
            provider.create_response(instructions='Test', input=context)
        messages = call.call_args.args[0]['messages']
        self.assertEqual(messages[0], context[0])
        self.assertEqual(messages[1]['content'][0]['source']['data'], DATA)
