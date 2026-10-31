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

    def test_opaque_authorization_keys_and_header_safety(self):
        key = 'AQ.synthetic-token.with.dots_0123-abc+/='  # Not a real credential.
        self.assertEqual(read_api_key(environ={'GEMINI_API_KEY': key}), key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            for value in (key, '"' + key + '"', "'" + key + "'"):
                path.write_text('GEMINI_API_KEY=' + value + '\n')
                self.assertEqual(read_api_key(path, {}), key)
        for invalid in ('key\nInjected:header', 'key\rInjected', 'key\x00',
                        'key\tother', 'key with spaces', 'キー', 'a' * 16385):
            with self.subTest(invalid_length=len(invalid)), self.assertRaises(ValueError):
                read_api_key(environ={'GEMINI_API_KEY': invalid})

    def test_multiturn_history_signatures_and_summary_isolation(self):
        calls = []

        def transport(model, contents, instructions, key, cancel, on_chunk):
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

    def test_streaming_delivers_text_before_completion_and_uses_one_message_id(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def transport(model, contents, instructions, key, cancel, on_chunk):
            on_chunk(answer('途中'))
            started.set()
            release.wait(3)
            on_chunk(answer('から続き'))
            return answer('途中から続き')

        session, events = self.make_session(transport)
        session.send('依頼')
        self.assertTrue(started.wait(2))
        self.assertTrue(session.busy)
        self.assertEqual([data['text'] for kind, data in events if kind == 'delta'], ['途中'])
        self.assertNotIn('assistant', [kind for kind, _ in events])
        self.assertNotIn('submitted', [kind for kind, _ in events])
        release.set()
        self.wait(session)
        messages = [data for kind, data in events if kind in ('delta', 'assistant')]
        self.assertEqual(len({data['item_id'] for data in messages}), 1)
        self.assertEqual(messages[-1]['text'], '途中から続き')
        self.assertEqual(events[-1][1]['status'], 'completed')

    def test_stream_failure_keeps_partial_text_out_of_history_and_does_not_retry(self):
        calls = []

        def transport(model, contents, instructions, key, cancel, on_chunk):
            calls.append(contents)
            on_chunk(answer('途中'))
            raise TimeoutError('private detail')

        session, events = self.make_session(transport)
        session.send('依頼')
        self.wait(session)
        self.assertEqual(len(calls), 1)
        self.assertFalse(session._history)
        self.assertIn('delta', [kind for kind, _ in events])
        self.assertNotIn('submitted', [kind for kind, _ in events])
        self.assertEqual(events[-1][1]['status'], 'failed')

    def test_fragmented_sse_text_thoughts_and_signatures_are_merged(self):
        chunks = [
            {'candidates': [{'content': {'parts': [{'text': '非表示', 'thought': True}]}}]},
            {'candidates': [{'content': {'parts': [{'text': '日本'}]}}]},
            {'candidates': [{'content': {'parts': [{'text': '語', 'thoughtSignature': 'signature'}]},
                             'finishReason': 'STOP'}]},
        ]
        wire = b': keep-alive\r\n\r\n' + b''.join(
            b'data: ' + json.dumps(chunk, ensure_ascii=False).encode() + b'\r\n\r\n' for chunk in chunks)

        class Fragmented(io.BytesIO):
            def read1(self, size):
                return super().read(1)

        opener = Mock()
        opener.open.return_value = Fragmented(wire)
        received = []
        with patch('chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
            result = generate(DEFAULT_MODEL, [], '', 'fake-key', threading.Event(), received.append)
        self.assertEqual(received, chunks)
        parts = result['candidates'][0]['content']['parts']
        self.assertEqual(parts[0], {'text': '非表示', 'thought': True})
        self.assertEqual(parts[1], {'text': '日本語', 'thoughtSignature': 'signature'})
        self.assertIn(':streamGenerateContent?alt=sse', opener.open.call_args.args[0].full_url)
        opener.open.return_value = io.BytesIO(b'data: {"error":{"message":"fake-key"}}\n\n')
        with patch('chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as raised:
                generate(DEFAULT_MODEL, [], '', 'fake-key', threading.Event())
            self.assertNotIn('fake-key', str(raised.exception))

    def test_sse_size_and_deadline_limits_and_multiline_events(self):
        from chat_foundation.gemini.session import _stream_events
        wire = b'data: {"candidates":\ndata: []}\n\n'
        with patch('chat_foundation.gemini.session.time.monotonic', return_value=0):
            self.assertEqual(list(_stream_events(io.BytesIO(wire), threading.Event(), 0)), [{'candidates': []}])
            with patch('chat_foundation.gemini.session.MAX_RESPONSE_BYTES', 5):
                with self.assertRaisesRegex(ValueError, '大きすぎ'):
                    list(_stream_events(io.BytesIO(wire), threading.Event(), 0))
        with patch('chat_foundation.gemini.session.time.monotonic', return_value=121):
            with self.assertRaises(TimeoutError):
                list(_stream_events(io.BytesIO(wire), threading.Event(), 0))

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
            args[-1](answer('中断前'))
            started.set()
            release.wait(3)
            args[-1](answer('古い回答'))
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
        self.assertEqual([data['text'] for kind, data in events if kind == 'delta'], ['中断前'])
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
        response = io.BytesIO(b'data: ' + json.dumps(answer()).encode() + b'\n\n')
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
        cancel = threading.Event()
        cancel.set()
        opener.reset_mock()
        with patch('chat_foundation.gemini.session.urllib.request.build_opener', return_value=opener):
            self.assertIsNone(generate(DEFAULT_MODEL, [], '', 'fake-key', cancel))
        opener.open.assert_not_called()

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
