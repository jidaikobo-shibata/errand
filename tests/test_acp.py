import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from chat_foundation.acp.session import Session


class ACPTests(unittest.TestCase):
    def make_session(self, dispatch=None, arguments=()):
        events, launches = [], []

        def launch(directory, instructions, summary=False):
            launches.append((directory, instructions, summary))
            return {'command': [sys.executable, str(Path(__file__).with_name('fake_acp_server.py')), *arguments],
                    'cwd': directory, 'excluded_models': ['auto'],
                    'environment': {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}}

        session = Session(lambda kind, data: events.append((kind, data)), launch=launch,
                          dispatch=dispatch or (lambda fn: fn()), instructions='work',
                          summary_instructions='summary-only', assistant_name='Offline', required_mode='default')
        self.addCleanup(session.close)
        session.load_models()
        self.until(lambda: not session._loading_models)
        return session, events, launches

    def until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('ACP operation timed out')
            time.sleep(.005)

    def test_delayed_interrupt_cannot_cancel_the_next_turn(self):
        session, events, _ = self.make_session()
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        original = session._interrupt
        def delayed(generation, turn_id):
            entered.set()
            release.wait(4)
            original(generation, turn_id)
            done.set()
        session._interrupt = delayed
        self.addCleanup(release.set)
        session.send('SHORT')
        self.until(lambda: session._prompt_future is not None)
        session.interrupt()
        self.until(entered.is_set)
        self.until(lambda: not session.busy)
        session.send('WAIT')
        self.until(lambda: session._prompt_future is not None)
        release.set()
        self.until(done.is_set)
        self.assertTrue(session.busy)
        self.assertFalse(session._interrupt_requested)
        session._interrupt = original
        session.interrupt()
        self.until(lambda: not session.busy)

    def test_mode_notices_are_hidden_only_during_mode_rpc(self):
        from errand.gemini_cli import is_mode_notice
        session, events, _ = self.make_session()
        session.mode_notice_filter = is_mode_notice
        session.approval_modes = (('default', 'Manual'), ('autoEdit', 'Edit'), ('yolo', 'All'))
        for mode in ('autoEdit', 'yolo', 'default'):
            events.clear()
            session.set_approval_mode(mode)
            session.send('hello')
            self.until(lambda: not session.busy)
            self.assertEqual(''.join(d['text'] for k, d in events if k == 'delta'), '回答です。')
            self.assertEqual([d['text'] for k, d in events if k == 'assistant'], ['回答です。'])
        events.clear()
        session.send('EXPLAIN_MODE')
        self.until(lambda: not session.busy)
        self.assertEqual([d['text'] for k, d in events if k == 'assistant'], ['[MODE_UPDATE] autoEdit'])
        self.assertFalse(is_mode_notice('説明: [MODE_UPDATE] autoEdit', 'autoEdit'))

    def test_native_approval_modes_apply_before_prompt_and_reset(self):
        session, events, _ = self.make_session()
        session.approval_modes = (('default', 'Manual'), ('autoEdit', 'Edit'), ('yolo', 'All'))
        with self.assertRaises(ValueError):
            session.set_approval_mode('unknown')
        for mode in ('autoEdit', 'yolo', 'default'):
            session.set_approval_mode(mode)
            session.send('hello')
            with self.assertRaises(Exception):
                session.set_approval_mode('default')
            self.until(lambda: not session.busy)
            self.assertEqual((Path(session._scratch.name) / 'mode.txt').read_text(), mode)
            self.assertEqual(events[-1][1]['status'], 'completed')
        session.set_approval_mode('autoEdit')
        session.reset()
        session.send('again')
        self.until(lambda: not session.busy)
        self.assertEqual((Path(session._scratch.name) / 'mode.txt').read_text(), 'autoEdit')
        session._available_modes = {'default'}
        with self.assertRaises(Exception):
            session.set_approval_mode('yolo')

    def test_rejected_mode_never_sends_prompt(self):
        session, events, _ = self.make_session(arguments=('--reject-mode',))
        session.approval_modes = (('default', 'Manual'), ('yolo', 'All'))
        session.set_approval_mode('yolo')
        session.send('must not run')
        self.until(lambda: not session.busy)
        self.assertFalse(any(kind in ('submitted', 'delta') for kind, _ in events))
        self.assertEqual(events[-1][1]['status'], 'failed')

    def test_attachment_policy_is_scoped_to_current_input_and_native_request(self):
        from errand.gemini_cli import approve_attached_read
        tool = {'kind': 'read', 'toolCallId': 'resolve-prompt-file-uuid',
                'title': 'Allow access to absolute path: /tmp/attached.txt'}
        for mode in ('autoEdit', 'yolo'):
            self.assertTrue(approve_attached_read(tool, ['/tmp/attached.txt'], mode))
        self.assertFalse(approve_attached_read(tool, ['/tmp/attached.txt'], 'default'))
        self.assertFalse(approve_attached_read(tool, ['/tmp/other.txt'], 'yolo'))
        self.assertFalse(approve_attached_read(tool, [], 'yolo'))
        self.assertFalse(approve_attached_read({**tool, 'kind': 'edit'}, ['/tmp/attached.txt'], 'yolo'))
        self.assertFalse(approve_attached_read({**tool, 'toolCallId': 'other'}, ['/tmp/attached.txt'], 'yolo'))

    def test_models_streaming_turns_and_reset(self):
        session, events, launches = self.make_session()
        self.assertEqual([entry['model'] for entry in session._models], ['offline-model'])
        session.set_approval_reviewer('auto_review')
        self.assertEqual(session.approvals_reviewer, 'user')
        session.send('hello')
        self.until(lambda: not session.busy)
        self.assertEqual(''.join(data['text'] for kind, data in events if kind == 'delta'), '回答です。')
        self.assertIn('submitted', [kind for kind, _ in events])
        self.assertEqual(events[-1][1]['status'], 'completed')
        session.reset()
        session.send('again')
        self.until(lambda: not session.busy)
        self.assertEqual(len(launches), 2)
        for text in ('/not-a-cli-command', '$not-a-shell-command'):
            session.send(text)
            self.until(lambda: not session.busy)
            self.assertFalse((Path(session._scratch.name) / 'slash-executed.txt').exists())

    def test_interrupt_during_setup_does_not_wait_for_model_timeout(self):
        session, events, _ = self.make_session(arguments=('--slow-model',))
        session.send('hello')
        self.until(lambda: bool(session._server._pending))
        self.assertIsNone(session._prompt_future)
        session.interrupt()
        self.until(lambda: not session.busy)
        self.assertEqual(events[-1][1]['status'], 'interrupted')
        self.assertNotIn('submitted', [kind for kind, _ in events])

    def test_agent_without_model_selection_uses_its_default(self):
        session, events, _ = self.make_session(arguments=('--no-models',))
        self.assertEqual(session._models[0]['model'], 'default')
        session.send('hello')
        self.until(lambda: not session.busy)
        self.assertEqual(events[-1][1]['status'], 'completed')

    def test_file_creation_requires_once_permission_and_rejects_forever(self):
        session, events, _ = self.make_session()
        target = Path(session._scratch.name) / 'output.txt'
        session.send('WRITE:' + str(target))
        self.until(lambda: bool(session._requests))
        self.assertFalse(target.exists())
        request = next(data for kind, data in events if kind == 'request')
        self.assertEqual(request['method'], 'agent/permission')
        self.assertEqual([o['kind'] for o in request['params']['options']], ['allow_once', 'reject_once'])
        with self.assertRaises(RuntimeError):
            session.answer(request['request_id'], {'option_id': 'forever'})
        with self.assertRaises(RuntimeError):
            session.answer(request['request_id'], {'option_id': 'yes'}, remember=True)
        session.answer(request['request_id'], {'option_id': 'yes'})
        self.until(lambda: not session.busy)
        self.assertEqual(target.read_text(), 'created')
        self.assertFalse(session.answer(request['request_id'], {'option_id': 'yes'}))

    def test_denial_cancellation_and_unknown_requests_never_write(self):
        for choice in ({'option_id': 'no'}, {'cancel': True}):
            session, events, _ = self.make_session()
            target = Path(session._scratch.name) / 'output.txt'
            session.send('WRITE:' + str(target))
            self.until(lambda: bool(session._requests))
            request_id = next(iter(session._requests))
            session.answer(request_id, choice)
            self.until(lambda: not session.busy)
            self.assertFalse(target.exists())
        session.send('UNSUPPORTED')
        self.until(lambda: not session.busy)
        self.assertFalse((Path(session._scratch.name) / 'unexpected.txt').exists())

    def test_always_only_options_are_denied(self):
        session, events, _ = self.make_session()
        target = Path(session._scratch.name) / 'output.txt'
        session.send('ALWAYS:' + str(target))
        self.until(lambda: not session.busy)
        self.assertFalse(target.exists())
        self.assertNotIn('request', [kind for kind, _ in events])

    def test_interrupt_and_disconnect_can_reconnect(self):
        session, events, _ = self.make_session()
        session.send('WAIT')
        self.until(lambda: session._prompt_future is not None)
        session.interrupt()
        self.until(lambda: not session.busy)
        self.assertEqual(events[-1][1]['status'], 'interrupted')
        session.send('CRASH')
        self.until(lambda: not session.busy)
        self.assertIsNone(session._server)
        session.send('reconnect')
        self.until(lambda: not session.busy)
        self.assertEqual(events[-1][1]['status'], 'completed')

    def test_summary_uses_separate_tool_free_launcher_and_no_parent_messages(self):
        session, events, launches = self.make_session()
        session.summarize('conversation')
        self.until(lambda: not session.busy)
        self.assertEqual(launches[-1][1:], ('summary-only', True))
        self.assertIn('summary', [kind for kind, _ in events])
        self.assertNotIn('user', [kind for kind, _ in events])
        self.assertFalse(session._text)

    def test_close_invalidates_queued_callbacks_and_removes_private_storage(self):
        callbacks = []
        session, events, _ = self.make_session(dispatch=callbacks.append)
        session.send('hello')
        self.until(lambda: not session.busy)
        directory = Path(session._scratch.name)
        session.close()
        for callback in callbacks:
            callback()
        self.assertEqual(events, [])
        self.assertFalse(directory.exists())

    def test_file_references_and_independent_import(self):
        session, events, _ = self.make_session()
        path = Path(session._scratch.name) / '日本語 % file.txt'
        path.write_text('offline')
        session.send('file', files=[str(path)])
        self.until(lambda: not session.busy)
        self.assertIn(str(path), next(data['text'] for kind, data in events if kind == 'user'))
        subprocess.run([sys.executable, '-c', 'import sys; import chat_foundation.acp.session; '
                        'assert not any(n == "errand" or n.startswith("errand.") or n == "gi" '
                        'for n in sys.modules)'], check=True)

    def test_close_with_pending_permission_never_creates_external_fixture(self):
        session, events, _ = self.make_session()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'not-created.txt'
            session.send('WRITE:' + str(target))
            self.until(lambda: bool(session._requests))
            request_id = next(iter(session._requests))
            session.close()
            self.assertFalse(session.answer(request_id, {'option_id': 'yes'}))
            self.assertFalse(target.exists())

    def test_idle_disconnect_reconnects_and_invalid_models_do_not_send(self):
        session, events, launches = self.make_session()
        with self.assertRaises(RuntimeError):
            session.send('not sent', model='unknown')
        self.assertNotIn('user', [kind for kind, _ in events])
        session._server._process.kill()
        self.until(lambda: session._server is None)
        session.send('reconnect')
        self.until(lambda: not session.busy)
        self.assertEqual(len(launches), 2)
        self.assertEqual(events[-1][1]['status'], 'completed')

    def test_launch_failure_does_not_expose_secret_exception_text(self):
        events = []
        def launch(*args, **kwargs):
            raise ValueError('synthetic-private-key')
        session = Session(lambda k, d: events.append((k, d)), launch=launch,
                          instructions='', summary_instructions='')
        self.addCleanup(session.close)
        session.load_models()
        self.until(lambda: not session._loading_models)
        self.assertIn('models_error', [kind for kind, _ in events])
        self.assertNotIn('synthetic-private-key', str(events))


class LauncherTests(unittest.TestCase):
    def test_private_runtime_and_readonly_mcp_settings(self):
        from errand.gemini_cli import Launcher, file_uri
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'source.json'
            original.write_text(json.dumps({'mcpServers': {'example': {'command': 'not-executed', 'trust': True}},
                                            'security': {'auth': {'selectedType': 'oauth-personal'}}}))
            before = original.read_bytes()
            with patch('errand.gemini_cli.find_gemini', return_value='/usr/bin/gemini'), \
                    patch('errand.gemini_cli.read_api_key', return_value='synthetic-key'):
                launcher = Launcher(mcp_config=str(original))
                spec = launcher(directory, 'product-instructions')
                summary = launcher(directory, 'summary-instructions', summary=True)
            config = Path(spec['environment']['GEMINI_CLI_HOME']) / '.gemini/settings.json'
            settings = json.loads(config.read_text())
            self.assertFalse(settings['mcpServers']['example']['trust'])
            self.assertEqual(settings['security']['auth']['selectedType'], 'gemini-api-key')
            self.assertNotIn('synthetic-key', config.read_text())
            self.assertNotIn('GEMINI_API_KEY', spec['environment'])
            self.assertEqual(spec['auth']['_meta']['api-key'], 'synthetic-key')
            self.assertEqual(original.read_bytes(), before)
            summary_config = Path(summary['environment']['GEMINI_CLI_HOME']) / '.gemini/settings.json'
            self.assertEqual(json.loads(summary_config.read_text())['tools']['core'], [])
            self.assertEqual(json.loads(summary_config.read_text())['mcpServers'], {})
            self.assertEqual(file_uri(root / '日本語 % file'), 'file://' + str(root / '日本語 % file'))
