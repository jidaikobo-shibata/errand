#!/usr/bin/env python3
"""Exercise the common UI with an offline ACP agent and temporary files."""
from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gio, GLib, Gdk
from chat_foundation.acp.session import Session

app = Application(isolated=True, smoke=True)
failures = []
sys.excepthook = lambda kind, error, traceback: (failures.append(error), sys.__excepthook__(kind, error, traceback))


def until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('ACP UI timed out')
        GLib.MainContext.default().iteration(False)
        time.sleep(.001)


def verify(_):
    try:
        original = app.window.current
        original.input.get_buffer().set_text('Codexの下書き')
        app.settings.set_approval_reviewer('auto_review')
        app.open_preferences()
        assert app.preferences.path.get_placeholder_text() == '空欄なら自動で検索します'
        assert app.preferences.gemini_path.get_placeholder_text() == app.preferences.path.get_placeholder_text()
        assert app.preferences.provider.get_model().get_n_items() == 2
        assert app.preferences.provider.get_model().get_string(1) == 'Gemini'
        until(lambda: not app.preferences._key_busy)
        prefs = app.preferences
        assert not prefs._key_saved
        prefs.key_entry.set_text('synthetic-gui-key')
        prefs.key_save.emit('clicked')
        until(lambda: not prefs._key_busy)
        assert prefs._key_saved and prefs.key_entry.get_text() == ''
        assert app.services.credential_store.load() == 'synthetic-gui-key'
        assert 'synthetic-gui-key' not in repr(app.settings.values)
        prefs.key_entry.set_text('replacement-gui-key')
        prefs.key_save.emit('clicked')
        until(lambda: not prefs._key_busy)
        assert app.services.credential_store.load() == 'replacement-gui-key'
        prefs.key_delete.emit('clicked')
        until(lambda: not prefs._key_busy)
        assert not prefs._key_saved
        assert app.services.credential_store.load() is None
        app.preferences.provider.set_selected(1)
        app.preferences.save()
        assert app.settings.get('ai-provider') == 'gemini-cli'
        assert app.preferences is None
        view = app.window.new_tab()
        assert isinstance(view.session, Session)
        assert not view.backend_label.get_visible()
        assert view.session.notice == ''
        assert view.session.approvals_reviewer == 'user'
        assert app.settings.get('approval-reviewer') == 'auto_review'
        assert view.approval_choice.get_visible()
        assert view.approval_choice.get_selected() == 0
        assert view.approval_choice.get_sensitive()
        assert original.input.get_buffer().get_char_count() == len('Codexの下書き')

        def launch(directory, instructions, summary=False):
            return {'command': [sys.executable, str(Path(__file__).resolve().parents[1] / 'tests/fake_acp_server.py')],
                    'cwd': directory, 'environment': {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}}

        view.session.launch = launch
        view.session.load_models()
        until(lambda: view.selected_model() is not None)
        view.approval_choice.set_selected(1)
        assert view.session.approval_mode == 'autoEdit'
        assert app.settings.get('gemini-approval-mode') == 'autoEdit'
        inherited = app.window.new_tab()
        assert inherited.session.approval_mode == 'autoEdit'
        assert inherited.approval_choice.get_selected() == 1
        assert app.settings.get('approval-reviewer') == 'auto_review'
        view.approval_choice.set_selected(2)
        assert view.session.approval_mode == 'yolo'
        assert app.settings.get('gemini-approval-mode') == 'yolo'
        assert inherited.session.approval_mode == 'autoEdit'
        view.approval_choice.set_selected(0)
        assert view.session.approval_mode == 'default'
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / '日本語 % file.txt'
            source.write_text('offline source')
            files = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(source))])
            assert view.drop_files(None, files, 0, 0)
            target = Path(directory) / 'created.txt'
            view.input.get_buffer().set_text('WRITE:' + str(target))
            view.send(None)
            until(lambda: bool(view.requests))
            assert not target.exists()
            request = next(iter(view.requests.values()))
            row = request.get_last_child()
            assert row.get_first_child().get_label() == '今回だけ許可'
            assert row.get_first_child().get_next_sibling().get_label() == '拒否'
            assert row.get_last_child().get_label() == '作業を中止'
            row.get_first_child().emit('clicked')
            until(lambda: target.exists() and not view.session.busy and not view.requests)
            until(lambda: view.input.get_buffer().get_char_count() == 0)
            assert not view.attachments
            assert 'Gemini\n回答です。' in view.conversation_text()
            answer = next(iter(view.messages.values()))
            assert answer.message_copy_button.get_visible()
            assert answer.timing_label.get_visible()
            view.create_summary(None)
            until(lambda: bool(view.summary_text))
            assert '要約です。' in view.summary_text
            denied = Path(directory) / 'denied.txt'
            view.input.get_buffer().set_text('WRITE:' + str(denied))
            view.send(None)
            until(lambda: bool(view.requests))
            row = next(iter(view.requests.values())).get_last_child()
            row.get_first_child().get_next_sibling().emit('clicked')
            until(lambda: not view.session.busy and not view.requests)
            assert not denied.exists()
            view.input.get_buffer().set_text('WAIT')
            view.send(None)
            until(lambda: view.session._prompt_future is not None)
            view.stop.emit('clicked')
            until(lambda: not view.session.busy)
            app.open_preferences()
            app.preferences.provider.set_selected(0)
            app.preferences.save()
            codex = app.window.new_tab()
            assert not isinstance(codex.session, Session)
            assert codex.session.approvals_reviewer == 'auto_review'
            assert original.input.get_buffer().get_char_count() == len('Codexの下書き')
            assert isinstance(view.session, Session)
            assert not view.backend_label.get_visible()
            assert view.session.notice == ''
            # Exercise the preserved Codex wire adapter through the same GTK widgets.
            codex.session.command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tests/fake_server.py')]
            codex.session.load_models()
            until(lambda: codex.selected_model() is not None)
            codex.input.get_buffer().set_text('approval')
            codex.send_button.emit('clicked')
            until(lambda: bool(codex.requests))
            row = next(iter(codex.requests.values())).get_last_child()
            row.get_first_child().get_next_sibling().emit('clicked')
            until(lambda: not codex.session.busy and bool(codex.messages))
            assert codex.approval_choice.get_selected() == 1
            assert codex.approval_choice.get_sensitive()
            codex_answer = list(codex.messages.values())[-1]
            assert codex_answer.message_copy_button.get_visible()
            assert codex_answer.timing_label.get_visible()
        print('PASS: ACP files, once approval, denial, interruption, summary, timing and Codex preservation')
    except Exception as error:
        failures.append(error)
    finally:
        app.quit()


app.connect('activate', verify)
app.run([sys.argv[0]])
if failures:
    raise failures[0]
