#!/usr/bin/env python3
"""Test configurable shortcuts without modifying real preferences."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib, Gdk
from errand.shortcuts import validate_shortcuts, bindings

app = Application(isolated=True, smoke=True)
failures = []


def verify():
    try:
        window = app.window
        keys = window.key_controller
        ctrl = Gdk.ModifierType.CONTROL_MASK
        alt = Gdk.ModifierType.ALT_MASK
        first = window.tabs.get_selected_page()
        window.new_tab()
        second = window.tabs.get_selected_page()
        assert keys.emit('key-pressed', Gdk.KEY_Page_Up, 0, ctrl)
        assert window.tabs.get_selected_page() is first
        assert keys.emit('key-pressed', Gdk.KEY_Tab, 0, ctrl)
        assert window.tabs.get_selected_page() is second
        app.open_preferences()
        prefs = app.preferences
        assert prefs.get_visible_page_name() == 'general'
        prefs.set_visible_page_name('keyboard')
        assert prefs.get_visible_page_name() == 'keyboard'
        prefs.capture(action='new-tab')
        prefs.capture_controller.emit('key-pressed', Gdk.KEY_n, 0, ctrl | alt)
        assert prefs.shortcut_overrides['new-tab'] == '<Control><Alt>n'
        prefs.set_shortcut('<Control><Alt>h', 'previous-tab')
        prefs.set_shortcut('<Control><Alt>l', 'next-tab')
        prefs.set_shortcut('<Control><Alt>k', 'previous-message')
        prefs.set_shortcut('<Control><Alt>j', 'next-message')
        prefs.set_shortcut('', 'close-tab')
        prefs.save()
        assert app.preferences is None
        assert keys.emit('key-pressed', Gdk.KEY_n, 0, ctrl | alt)
        assert window.tabs.get_n_pages() == 3
        assert not keys.emit('key-pressed', Gdk.KEY_t, 0, ctrl)
        assert not keys.emit('key-pressed', Gdk.KEY_w, 0, ctrl)
        assert keys.emit('key-pressed', Gdk.KEY_h, 0, ctrl | alt)
        assert window.tabs.get_selected_page() is second
        assert keys.emit('key-pressed', Gdk.KEY_l, 0, ctrl | alt)
        assert window.tabs.get_selected_page() is not second
        assert not keys.emit('key-pressed', Gdk.KEY_Page_Up, 0, ctrl)
        assert not keys.emit('key-pressed', Gdk.KEY_Tab, 0, ctrl)
        view = window.current
        view.add_message('あなた', '依頼1')
        view.add_message('Codex', '回答1')
        view.add_message('あなた', '依頼2')
        assert keys.emit('key-pressed', Gdk.KEY_k, 0, ctrl | alt)
        assert view.navigation_index() == 1
        assert keys.emit('key-pressed', Gdk.KEY_j, 0, ctrl | alt)
        assert view.navigation_index() == 2
        assert 'K' in view.navigation_buttons['previous'].get_tooltip_text()
        app.open_preferences()
        prefs = app.preferences
        prefs.set_shortcut('<Control><Alt>n', 'next-tab')
        prefs.save()
        assert app.preferences is prefs
        assert '同じキー' in prefs.keyboard_message.get_text()
        prefs.reset_shortcut('next-tab')
        prefs.reset_shortcut('new-tab')
        prefs.save()
        assert app.preferences is None
        assert keys.emit('key-pressed', Gdk.KEY_t, 0, ctrl)
        assert not keys.emit('key-pressed', Gdk.KEY_n, 0, ctrl | alt)
        original = app.settings.get('shortcuts')
        app.change_font_size(1)
        assert app.settings.get('shortcuts') == original
        try:
            validate_shortcuts({'new-tab': '<Control>KP_Enter'})
            raise AssertionError('alias collision not rejected')
        except ValueError:
            pass
        assert bindings('new-tab', {}, 'darwin')[0] == '<Meta>t'
        assert bindings('new-tab', {'new-tab': ''}, 'darwin') == ()
        print('PASS: keyboard page, capture, remapping, old-key removal, tabs, messages, disable/reset and duplicate detection')
    except Exception as exc:
        failures.append(exc)
    finally:
        app.quit()
    return False


app.connect('activate', lambda _: GLib.timeout_add(60, verify))
app.run([])
if failures:
    raise failures[0]
