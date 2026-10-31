from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error

from chat_foundation.gemini.credentials import read_api_key
from chat_foundation.gemini.session import DEFAULT_MODEL, Session, generate, _NoRedirect


def answer(text='回答', finish='STOP'):
    return {'candidates': [{'content': {'role': 'model', 'parts': [
        {'text': text, 'thoughtSignature': 'opaque-signature'}]}, 'finishReason': finish}]}


class GeminiTests(unittest.TestCase):
    def make_session(self, transport=None, dispatch=None):
        events = []
        session = Session(lambda kind, data: events.append((kind, data)),
                          instructions='会話指示', summary_instructions='要約指示',
                          key_reader=lambda path: 'fake-key',
                          transport=transport or Mock(return_value=answer()),
                          **({'dispatch': dispatch} if dispatch else {}))
        self.addCleanup(session.close)
        return session, events

    def wait(self, session):
        worker = session._worker
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def test_credentials_read_only_explicit_source_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('OTHER=value\nexport GEMINI_API_KEY="file-key"\n')
            before = path.read_bytes()
            self.assertEqual(read_api_key(path, {}), 'file-key')
            self.assertEqual(read_api_key(path, {'GEMINI_API_KEY': 'env-key'}), 'env-key')
            self.assertEqual(path.read_bytes(), before)
            for text in ('GEMINI_API_KEY=$(touch injected)', 'GEMINI_API_KEY="broken',
                         'GEMINI_API_KEY=', 'GEMINI_API_KEY=with space'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    read_api_key(path, {})
            with self.assertRaises(ValueError):
                read_api_key(Path(directory) / 'missing', {})
            path.write_text('x' * 65537)
            with self.assertRaises(ValueError):
                read_api_key(path, {})

    def test_model_loading_is_local_and_missing_key_disables_send(self):
        transport = Mock()
        session, events = self.make_session(transport)
        session.load_models()
        transport.assert_not_called()
        catalogue = next(data for kind, data in events if kind == 'models')
        self.assertEqual(catalogue['default_model'], DEFAULT_MODEL)
        self.assertFalse(session.supports_files)
        session._key_reader = Mock(side_effect=ValueError('missing'))
        events.clear()
        session.load_models()
        self.assertIn('models_error', [kind for kind, _ in events])
        with self.assertRaises(ValueError):
            session.send('secret')
        self.assertFalse(session.busy)
        transport.assert_not_called()

    def test_multiturn_history_signatures_and_summary_isolation(self):
        calls = []

        def transport(model, contents, instructions, key, cancel):
            calls.append((model, deepcopy(contents), instructions))
            return answer()

        session, events = self.make_session(transport)
        session.send('最初')
        self.wait(session)
        session.send('続き')
        self.wait(session)
        self.assertEqual(calls[0][0], DEFAULT_MODEL)
        self.assertEqual([item['role'] for item in calls[1][1]], ['user', 'model', 'user'])
        self.assertEqual(calls[1][1][1]['parts'][0]['thoughtSignature'], 'opaque-signature')
        history = deepcopy(session._history)
        session.summarize('要約対象')
        self.wait(session)
        self.assertEqual(calls[-1][2], '要約指示')
        self.assertEqual(len(calls[-1][1]), 1)
        self.assertEqual(history, session._history)
        self.assertIn('summary', [kind for kind, _ in events])
        session.reset()
        session.send('新規')
        self.wait(session)
        self.assertEqual(len(calls[-1][1]), 1)

    def test_failure_is_not_retried_and_failed_prompt_does_not_enter_history(self):
        transport = Mock(side_effect=RuntimeError('fake-key sensitive detail'))
        session, events = self.make_session(transport)
        session.send('依頼')
        self.wait(session)
        transport.assert_called_once()
        self.assertEqual(session._history, [])
        self.assertNotIn('submitted', [kind for kind, _ in events])
        self.assertNotIn('fake-key', str(events))
        self.assertNotIn('sensitive detail', str(events))
        self.assertFalse(session.busy)

    def test_blocked_response_is_an_error_and_thoughts_are_not_displayed(self):
        transport = Mock(return_value={'promptFeedback': {'blockReason': 'SAFETY'}})
        session, events = self.make_session(transport)
        session.send('依頼')
        self.wait(session)
        self.assertIn('error', [kind for kind, _ in events])
        self.assertFalse(session._history)
        response = answer()
        response['candidates'][0]['content']['parts'].insert(0, {'text': 'private thought', 'thought': True})
        transport.return_value = response
        session.send('依頼')
        self.wait(session)
        self.assertEqual(next(data['text'] for kind, data in events if kind == 'assistant'), '回答')

    def test_interrupt_close_and_reset_suppress_stale_results(self):
        started, release = threading.Event(), threading.Event()

        def transport(*args):
            started.set()
            release.wait(3)
            return answer('古い回答')

        session, events = self.make_session(transport)
        session.send('依頼')
        self.assertTrue(started.wait(2))
        worker = session._worker
        with self.assertRaises(ValueError):
            session.send('重複')
        session.interrupt()
        session.reset()
        with self.assertRaises(ValueError):
            session.send('まだ通信中')
        release.set()
        worker.join(3)
        self.assertFalse(session._history)
        self.assertNotIn('assistant', [kind for kind, _ in events])
        self.assertNotIn('submitted', [kind for kind, _ in events])
        session.close(wait=False)
        with self.assertRaises(ValueError):
            session.send('終了後')

    def test_queued_events_are_discarded_after_reset_and_close(self):
        callbacks = []
        session, events = self.make_session(dispatch=callbacks.append)
        session.send('依頼')
        self.wait(session)
        session.reset()
        for callback in callbacks:
            callback()
        self.assertEqual([kind for kind, _ in events], ['reset'])
        session.close()
        session.load_models()
        self.assertEqual([kind for kind, _ in events], ['reset'])

    def test_transport_uses_header_not_url_and_no_tools_or_redirects(self):
        response = io.BytesIO(json.dumps(answer()).encode())
        opener = Mock()
        opener.open.return_value = response
        with patch('chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
            self.assertEqual(generate(DEFAULT_MODEL, [{'role': 'user', 'parts': [{'text': 'Hi'}]}],
                                      '指示', 'fake-key', threading.Event()), answer())
        request = opener.open.call_args.args[0]
        self.assertNotIn('fake-key', request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), 'fake-key')
        payload = json.loads(request.data)
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['systemInstruction']['parts'][0]['text'], '指示')
        self.assertIsNone(_NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_http_errors_timeouts_and_invalid_json_are_sanitized(self):
        for failure, expected in (
            (urllib.error.HTTPError('https://example/fake-key', 429, 'secret', {}, io.BytesIO()), '429'),
            (TimeoutError('fake-key'), '30秒'),
            (urllib.error.URLError('fake-key'), 'ネットワーク'),
        ):
            opener = Mock()
            opener.open.side_effect = failure
            with self.subTest(expected=expected), patch(
                    'chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
                with self.assertRaises(ValueError) as raised:
                    generate(DEFAULT_MODEL, [], '', 'fake-key', threading.Event())
                self.assertIn(expected, str(raised.exception))
                self.assertNotIn('fake-key', str(raised.exception))
                opener.open.assert_called_once()
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'not-json')
        with patch('chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, '読み取れません'):
                generate(DEFAULT_MODEL, [], '', 'fake-key', threading.Event())

    def test_independent_of_product_and_gtk(self):
        code = ('import sys; from chat_foundation.gemini.session import Session; '
                'assert not any(m == "errand" or m.startswith("errand.") or m == "gi" '
                'for m in sys.modules)')
        subprocess.run([sys.executable, '-c', code], check=True)

    def test_provider_settings_roundtrip_and_old_defaults(self):
        from errand.settings import Settings, validate
        from errand.services import AIServices
        from errand.product import ERRAND
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(path=Path(directory) / 'settings.json')
            self.assertEqual(settings.get('ai-provider'), 'codex')
            settings.save({'ai-provider': 'gemini', 'gemini-env-path': '/tmp/example.env'})
            settings.set_font_size(12)
            restored = Settings(path=settings.path)
            self.assertEqual(restored.get('ai-provider'), 'gemini')
            self.assertEqual(restored.get('gemini-env-path'), '/tmp/example.env')
            services = AIServices(ERRAND, restored)
            session = services.conversation(lambda *_: None, dispatch=lambda fn: fn(), codex=None, command=None)
            self.addCleanup(session.close)
            self.assertIsInstance(session, Session)
            self.assertEqual(session.credentials_path, '/tmp/example.env')
            restored.save({'ai-provider': 'codex'})
            codex = services.conversation(lambda *_: None, dispatch=lambda fn: fn(), codex=None, command=None)
            self.addCleanup(codex.close)
            self.assertNotIsInstance(codex, Session)
            self.assertIsInstance(session, Session)
            for invalid in ({'ai-provider': 'unknown'}, {'gemini-env-path': 'relative'}):
                with self.assertRaises(ValueError):
                    validate(invalid)
