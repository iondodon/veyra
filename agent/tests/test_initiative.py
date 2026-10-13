import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_chat import WebChat
from initiative import Initiative
from providers import ProviderResponse, ShellAction, ShellCall
import bootstrap


class InitiativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.chat = WebChat(self.temp.name)
        self.schedule = self.chat.initiative

    def enable(self):
        self.schedule.configure(dict(enabled=True, interval_minutes=15, quiet_start=22, quiet_end=8))
        self.schedule.interacted(now=1000)

    def test_opt_in_and_validation(self):
        self.assertFalse(self.schedule.due(5, now=9000, hour=12))
        for changes in ({'enabled':'true'}, {'interval_minutes':True}, {'quiet_start':24}):
            with self.assertRaises(ValueError):
                self.schedule.configure(dict(enabled=True, interval_minutes=15, quiet_start=22, quiet_end=8, **{}) | changes)
        self.assertFalse(self.schedule.settings()['enabled'])

    def test_interval_quiet_hours_and_no_unanswered_repeat(self):
        self.enable()
        self.assertFalse(self.schedule.due(5, now=1800, hour=12))
        self.assertFalse(self.schedule.due(5, now=1900, hour=23))
        self.assertFalse(self.schedule.due(5, now=1900, hour=7))
        self.assertTrue(self.schedule.due(5, now=1900, hour=8))
        self.schedule.reserve(5, now=1900)
        self.assertFalse(self.schedule.due(5, now=4000, hour=12))
        self.assertTrue(self.schedule.due(6, now=4000, hour=12))
        restored = Initiative(self.chat.database)
        self.assertEqual(restored.settings(), self.schedule.settings())
        self.assertFalse(restored.due(5, now=4000, hour=12))

    def test_daytime_quiet_and_disabled_quiet(self):
        self.enable()
        self.schedule.data.update(quiet_start=9, quiet_end=17)
        self.assertFalse(self.schedule.due(5, now=4000, hour=12))
        self.assertTrue(self.schedule.due(5, now=4000, hour=18))
        self.schedule.data.update(quiet_start=9, quiet_end=9)
        self.assertTrue(self.schedule.due(5, now=4000, hour=12))

    def test_manual_and_rate_limit(self):
        self.assertTrue(self.schedule.due(0, now=1000, hour=23, manual=True))
        self.schedule.reserve(0, now=1000)
        self.assertFalse(self.schedule.due(0, now=1059, manual=True))
        self.assertTrue(self.schedule.due(0, now=1060, manual=True))

    def test_dispatch_guards_and_no_fake_user_message(self):
        payload = {'manual':True, 'available':True}
        self.assertFalse(self.chat.check_in(payload)[1]['started'])
        self.chat.ready = True
        self.chat.configuration['connected'] = True
        self.chat.busy = True
        self.assertFalse(self.chat.check_in(payload)[1]['started'])
        self.chat.busy = False
        self.chat.approval_id = 'pending'
        self.assertFalse(self.chat.check_in(payload)[1]['started'])
        self.chat.approval_id = None
        self.assertFalse(self.chat.check_in({'manual':True, 'available':False})[1]['started'])
        self.assertEqual(self.chat.check_in(payload), (202, {'started':True}))
        self.assertTrue(self.chat.inbox.get_nowait()['initiative'])
        self.assertEqual(self.chat.context_window(), [])
        self.chat.agent_event({'type':'message', 'kind':'checkin', 'text':'An idea'})
        self.chat.agent_event({'type':'done'})
        self.assertFalse(self.chat.busy)
        self.assertEqual(self.chat.context_window(), [{'role':'assistant','content':'An idea'}])

    def test_automatic_dispatch_and_unanswered_guard(self):
        self.enable()
        self.chat.ready = True
        self.chat.configuration['connected'] = True
        self.chat.publish('Help with our project', role='user')
        with patch('initiative.time.time', return_value=4000), patch('initiative.datetime') as clock:
            clock.fromtimestamp.return_value.hour = 12
            self.assertEqual(self.chat.check_in({'manual':False, 'available':True})[0], 202)
        self.chat.agent_event({'type':'message', 'kind':'checkin', 'text':'How is the project going?'})
        self.chat.agent_event({'type':'done'})
        with patch('initiative.time.time', return_value=9000), patch('initiative.datetime') as clock:
            clock.fromtimestamp.return_value.hour = 12
            self.assertEqual(self.chat.check_in({'manual':False, 'available':True}), (200, {'started':False}))
            self.chat.configuration['connected'] = False
            self.assertFalse(self.chat.check_in({'manual':True, 'available':True})[1]['started'])

    def test_empty_attempt_retries_without_blocking_for_reply(self):
        self.enable()
        self.chat.ready = True
        self.chat.configuration['connected'] = True
        self.chat.publish('A project', role='user')
        with patch('initiative.time.time', return_value=4000), patch('initiative.datetime') as clock:
            clock.fromtimestamp.return_value.hour = 12
            self.assertEqual(self.chat.check_in({'manual':False, 'available':True})[0], 202)
            self.chat.agent_event({'type':'done'})
            self.assertIn('minute', self.schedule.status(self.chat.latest_user_id(), now=4100, hour=12))
        restored = Initiative(self.chat.database)
        self.assertTrue(restored.due(self.chat.latest_user_id(), now=4900, hour=12))
        with patch('initiative.time.time', return_value=4900), patch('initiative.datetime') as clock:
            clock.fromtimestamp.return_value.hour = 12
            self.assertEqual(self.chat.check_in({'manual':False, 'available':True})[0], 202)

    def test_status_explains_paused_quiet_wait_and_reply(self):
        self.assertIn('paused', self.schedule.status(5, now=4000, hour=12))
        self.enable()
        self.assertIn('Quiet hours', self.schedule.status(5, now=4000, hour=23))
        self.assertIn('inactivity', self.schedule.status(5, now=1100, hour=12))
        self.schedule.reserve(5, now=1900)
        self.assertIn('reply', self.schedule.status(5, now=4000, hour=12))
        self.assertIn('initiative_status', self.chat.snapshot(0)['status'])

    def test_pause_suppresses_inflight_checkin(self):
        self.chat.configure_initiative(dict(enabled=False, interval_minutes=120, quiet_start=22, quiet_end=8))
        self.chat.agent_event({'type':'message','kind':'checkin','text':'Late arrival'})
        self.assertEqual(self.chat.context_window(), [])

    def test_model_has_no_tools_or_execution_or_chain_change(self):
        with patch.object(bootstrap, 'STATE', Path(self.temp.name)), patch.object(bootstrap, 'send') as send:
            conversation = bootstrap.Conversation()
            provider = Mock()
            conversation.provider = provider
            conversation.previous_response_id = 'keep'
            provider.create_response.return_value = ProviderResponse(id='new', output=[
                ShellCall(call_id='bad', action=ShellAction(commands=['false']))], output_text='A useful idea?')
            with patch.object(bootstrap, 'run_local') as run:
                conversation.check_in([{'role':'user', 'content':'Our project'}])
                run.assert_not_called()
            self.assertEqual(provider.create_response.call_args.kwargs['tools'], [])
            self.assertNotIn('previous_response_id', provider.create_response.call_args.kwargs)
            self.assertIsNone(conversation.pending)
            self.assertEqual(conversation.previous_response_id, 'keep')
            send.assert_called_once_with('A useful idea?', kind='checkin')
            provider.create_response.return_value = ProviderResponse(id='new', output=[], output_text='SKIP')
            send.reset_mock()
            conversation.check_in([])
            send.assert_not_called()
            conversation.pending = ('approval',)
            provider.reset_mock()
            conversation.check_in([])
            provider.create_response.assert_not_called()
