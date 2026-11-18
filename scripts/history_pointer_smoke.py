#!/usr/bin/env python3
"""Check pointer focus stability and keyboard focus scrolling on private Wayland."""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chat_foundation.gtk.conversation import Conversation, Gdk, Gio, GLib, Gtk

app = Gtk.Application(application_id='jp.jidaikobo.HistoryPointerTest',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
errors = []


def settle():
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(.001)


def run(_):
    window = Gtk.ApplicationWindow(application=app, default_width=700, default_height=650)
    view = None
    services = Mock()
    services.conversation.return_value = Mock(busy=False, approvals_reviewer='user',
                                              approval_modes=(), startup_message=None)
    settings = Mock()
    settings.get.return_value = 'user'

    def menu(view):
        button = Gtk.MenuButton()
        button.menu = Gtk.Popover()
        button.set_popover(button.menu)
        return button

    host = SimpleNamespace(
        product=SimpleNamespace(startup_tips=('案内',), setup_description='準備'),
        services=services, command=None, runtime=Mock(active=False), smoke=True,
        settings=settings, create_bookmark_menu=menu)
    try:
        view = Conversation(window, host, None)
        window.current = view
        window.set_child(view)
        first = view.add_message('Codex', '段落の文章\n' * 60)
        second = view.add_message('Codex', '次の段落\n' * 60)
        window.present()
        settle()
        adjustment = view.scroll.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size() + 300
        view._scroll_setting = True
        adjustment.set_value(200)
        view._scroll_setting = False
        view._follow_bottom = True
        view.queue_scroll_bottom()  # A pending streaming scroll must also yield.
        view.history_pointer.emit('pressed', 1, 20., 20.)
        window.set_focus(first)
        settle()
        assert abs(adjustment.get_value() - 200) < 1
        assert not view._follow_bottom
        assert not view.history_viewport.get_scroll_to_focus()
        first.select_region(0, 5)
        assert first.get_selection_bounds()[0]
        # Restoring keyboard behavior must allow an off-screen target into view.
        assert not view.history_keys.emit('key-pressed', Gdk.KEY_Tab, 0, Gdk.ModifierType(0))
        assert view.history_viewport.get_scroll_to_focus()
        window.set_focus(second)
        settle()
        assert abs(adjustment.get_value() - 200) > 1
        view.history_pointer.emit('pressed', 1, 20., 20.)
        before = adjustment.get_value()
        first.set_markup('<a href="https://example.com/">リンク</a>\n' + '文章\n' * 60)
        settle()
        before = adjustment.get_value()
        window.set_focus(first)
        settle()
        assert abs(adjustment.get_value() - before) < 1
        activated = []
        first.connect('activate-link', lambda _, uri: activated.append(uri) or True)
        assert first.emit('activate-link', 'https://example.com/')
        assert activated == ['https://example.com/']
        window.set_focus(view.input)
        view._follow_bottom = True
        view.history_keys.emit('key-pressed', Gdk.KEY_Left, 0, Gdk.ModifierType(0))
        assert view._follow_bottom  # Editing the prompt does not stop streaming.
        print('PASS: pointer focus/selection/link stability, pending scroll and keyboard scrolling')
    except Exception as error:
        errors.append(error)
    finally:
        if view is not None:
            view.close()
        window.destroy()
        GLib.idle_add(app.quit)


app.connect('activate', run)
app.run([])
if errors:
    raise errors[0]
