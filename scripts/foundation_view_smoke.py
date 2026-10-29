#!/usr/bin/env python3
"""Exercise the shared view with a minimal host, without importing Errand."""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chat_foundation.gtk.conversation import Conversation, Gio, GLib, Gtk

app = Gtk.Application(application_id='jp.jidaikobo.FoundationTest',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
failures = []


def run(_):
    view = None
    window = Gtk.ApplicationWindow(application=app)
    session = Mock(busy=False, approvals_reviewer='user')
    login = Mock()
    runtime = Mock(active=False)
    settings = Mock()
    settings.get.return_value = 'user'
    services = Mock()
    services.conversation.return_value = session
    services.login.return_value = login

    def menu(conversation):
        button = Gtk.MenuButton()
        button.menu = Gtk.Popover()
        button.set_popover(button.menu)
        return button

    host = SimpleNamespace(
        product=SimpleNamespace(startup_tips=('共通画面の案内',), setup_description='準備の案内'),
        services=services, command=None, runtime=runtime, smoke=True, settings=settings,
        create_bookmark_menu=menu)
    try:
        view = Conversation(window, host, None)
        window.current = view
        window.set_child(view)
        window.present()
        assert view.status.get_text() == '共通画面の案内'
        assert view.setup_description.get_text() == '準備の案内'
        session.load_models.assert_not_called()
        services.conversation.assert_called_once()
        view.input.get_buffer().set_text('依頼')
        buffer = view.input.get_buffer()
        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        clipboard = Mock()
        clipboard.read_text_finish.return_value = '長' * 1_820_000
        view.prompt_clipboard_ready(clipboard, None)
        assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == '依頼'
        assert buffer.get_has_selection()
        assert 'ファイル' in view.status.get_text()
        clipboard.read_text_finish.return_value = '短いお願い'
        view.prompt_clipboard_ready(clipboard, None)
        assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == '短いお願い'
        buffer.insert_at_cursor('\n' * 2_000)
        assert buffer.get_line_count() == 1
        buffer.set_text('a' * 100_000)
        buffer.insert_at_cursor('b')
        assert buffer.get_char_count() == 100_000
        buffer.set_text('依頼')
        # Exercise GTK's paste action and asynchronous clipboard read as well.
        clipboard = view.input.get_clipboard()
        clipboard.set('貼り付け')
        buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        view.input.emit('paste-clipboard')
        deadline = time.monotonic() + 3
        while buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) != '貼り付け':
            if time.monotonic() > deadline:
                raise AssertionError('Clipboard paste timed out')
            GLib.MainContext.default().iteration(False)
            time.sleep(.001)
        view.event('user', {'text': '依頼'})
        view.event('assistant', {'item_id': 'answer', 'text': '回答'})
        assert '依頼' in view.conversation_text()
        assert '回答' in view.conversation_text()
        assert not any(name == 'errand' or name.startswith('errand.') for name in sys.modules)
        view.close()
        session.close.assert_called_once_with(wait=False)
        login.close.assert_called_once()
        runtime.unsubscribe.assert_called_once_with(view.setup_event)
        print('PASS: shared conversation, minimal host, messages and cleanup without Errand', flush=True)
    except Exception as error:
        failures.append(error)
    finally:
        if view is not None and not view.closed:
            view.close()
        window.destroy()
        GLib.idle_add(app.quit)


app.connect('activate', run)
app.run([sys.argv[0]])
if failures:
    raise failures[0]
