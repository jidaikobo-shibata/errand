#!/usr/bin/env python3
"""Exercise provider selection and Gemini chat with fake credentials and transport."""
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib
from chat_foundation.gemini.session import Session, DEFAULT_MODEL

app = Application(isolated=True, smoke=True)
failures = []
stream_release = threading.Event()
sys.excepthook = lambda kind, error, traceback: (failures.append(error), sys.__excepthook__(kind, error, traceback))


def drain_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('GUI event timed out')
        GLib.MainContext.default().iteration(False)
        time.sleep(.001)


def verify(_):
    try:
        original = app.window.current
        original.input.get_buffer().set_text('Codexの下書き')
        with tempfile.TemporaryDirectory() as directory:
            credentials = Path(directory) / 'key.env'
            credentials.write_text('GEMINI_API_KEY=fake-key\n')
            app.open_preferences()
            prefs = app.preferences
            prefs.provider.set_selected(2)
            prefs.gemini_env.set_text(str(credentials))
            prefs.save()
            assert app.preferences is None
            view = app.window.new_tab()
            assert isinstance(view.session, Session)
            assert not isinstance(original.session, Session)
            assert original.input.get_buffer().get_char_count() == len('Codexの下書き')
            assert not view.approval_choice.get_visible()
            assert '文章の会話' in view.backend_label.get_text()
            view.session.load_models()
            drain_until(lambda: view.selected_model() is not None)
            assert view.selected_model()['model'] == DEFAULT_MODEL
            assert not view.drop_files(None, None, 0, 0)
            assert not view.attachments
            assert not view.prompt_fits('長' * 100_001)
            assert '分割' in view.status.get_text()
            response = {'candidates': [{'content': {'parts': [{'text': '日本語の回答'}]}, 'finishReason': 'STOP'}]}
            def stream(model, contents, instructions, key, cancel, on_chunk):
                on_chunk({'candidates': [{'content': {'parts': [{'text': '日本語'}]}}]})
                stream_release.wait(5)
                return response
            transport = Mock(side_effect=stream)
            view.session._transport = transport
            view.input.get_buffer().set_text('こんにちは')
            view.send(None)
            drain_until(lambda: '日本語' in view.conversation_text())
            assert view.session.busy
            partial = next(iter(view.messages.values()))
            assert not partial.message_copy_button.get_visible()
            assert not partial.timing_label.get_visible()
            stream_release.set()
            drain_until(lambda: '日本語の回答' in view.conversation_text() and not view.session.busy)
            drain_until(lambda: view.input.get_buffer().get_char_count() == 0)
            assert 'Gemini\n' in view.conversation_text()
            assert 'Codex\n' not in view.conversation_text()
            content = next(iter(view.messages.values()))
            drain_until(lambda: content.message_copy_button.get_visible())
            assert content.copy_group == [content]
            assert content.timing_label.get_visible()
            assert '初字' in content.timing_label.get_text()
            assert '最初の文字受信から終了まで' in content.timing_label.get_tooltip_text()
            transport.assert_called_once()
            first_timing = content.timing_label.get_text()
            view.create_summary(None)
            drain_until(lambda: bool(view.summary_text))
            assert view.summary_copy_button.get_visible()
            assert content.timing_label.get_text() == first_timing
            view.input.get_buffer().set_text('失敗する依頼')
            transport.side_effect = RuntimeError('fake-key')
            view.send(None)
            drain_until(lambda: not view.session.busy and 'エラー' in view.conversation_text())
            assert view.input.get_buffer().get_char_count() == len('失敗する依頼')
            assert 'fake-key' not in view.conversation_text()
            credentials.unlink()
            missing = app.window.new_tab()
            missing.session.load_models()
            drain_until(lambda: 'GEMINI_API_KEY' in missing.model_status.get_text())
            assert not missing.send_button.get_sensitive()
            assert not missing.setup_box.get_visible()
            assert not missing.login_box.get_visible()
            app.open_preferences()
            app.preferences.provider.set_selected(0)
            app.preferences.save()
            codex = app.window.new_tab()
            assert not isinstance(codex.session, Session)
            assert isinstance(view.session, Session)
            assert codex.approval_choice.get_visible()
            print('PASS: provider preferences, independent tabs, Gemini streaming, timing, summary, file rejection and failure drafts')
    except Exception as error:
        failures.append(error)
    finally:
        stream_release.set()
        app.quit()


app.connect('activate', verify)
app.run([sys.argv[0]])
if failures:
    raise failures[0]
