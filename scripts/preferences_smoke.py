#!/usr/bin/env python3
"""GTK preferences checks with an isolated store and no live Codex calls."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gdk, GLib, Gtk
from errand.preferences import mac_key, load_settings
from errand.math_widget import MathBlock
from errand.markdown import Block

app = Application(isolated=True, smoke=True)
failures = []


def run(_):
    try:
        view = app.window.current
        assert not view.input.get_accepts_tab()
        assert app.window.new.get_label() == '＋'
        assert app.window.new.get_icon_name() is None
        view.input.grab_focus()
        before = view.input.get_buffer().get_char_count()
        assert view.input.emit('move-focus', Gtk.DirectionType.TAB_FORWARD) is None
        assert not view.input.has_focus()
        assert view.input.get_buffer().get_char_count() == before
        view.input.grab_focus()
        view.input.emit('move-focus', Gtk.DirectionType.TAB_BACKWARD)
        assert not view.input.has_focus()
        assert view.input.get_buffer().get_char_count() == before
        math = MathBlock(Block('math', r'\frac{1}{2}'))
        view.history.append(math)
        original_width = math.layout.width
        app.open_preferences()
        prefs = app.preferences
        prefs.capture()
        prefs.capture_controller.emit('key-pressed', Gdk.KEY_F5, 0, Gdk.ModifierType.SUPER_MASK)
        assert prefs.shortcut_value == '<Super>F5'
        prefs.font.set_value(16)
        prefs.path.set_text('relative-invalid-path')
        prefs.save()
        assert prefs.message.get_text()
        assert app.settings.get('font-size') == 11
        prefs.path.set_text('/bin/sh')
        prefs.save()
        assert app.preferences is None
        assert app.settings.get('font-size') == 16
        assert math.layout.width > original_width
        assert app.codex == '/bin/sh'
        # Zoom also works in the editor and preserves its contents and Codex path.
        view.input.grab_focus()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        for key, state, expected in ((Gdk.KEY_plus, ctrl | Gdk.ModifierType.SHIFT_MASK, 17),
                                     (Gdk.KEY_minus, ctrl, 16),
                                     (Gdk.KEY_equal, ctrl, 17),
                                     (Gdk.KEY_KP_Subtract, ctrl, 16),
                                     (Gdk.KEY_0, ctrl, 11)):
            assert app.window.key_controller.emit('key-pressed', key, 0, state)
            assert app.settings.get('font-size') == expected
            assert app.codex == '/bin/sh'
            assert view.input.get_buffer().get_char_count() == before
        app.settings.set_font_size(28)
        app.change_font_size(1)
        assert app.settings.get('font-size') == 28
        app.settings.set_font_size(8)
        app.change_font_size(-1)
        assert app.settings.get('font-size') == 8
        app.change_font_size(0)
        assert app.settings.get('font-size') == 11
        with patch('errand.ui.sys.platform', 'darwin'):
            assert app.window.key_controller.emit('key-pressed', Gdk.KEY_plus, 0, Gdk.ModifierType.META_MASK)
            assert app.settings.get('font-size') == 12
            assert app.window.key_controller.emit('key-pressed', Gdk.KEY_0, 0, Gdk.ModifierType.META_MASK)
            assert app.settings.get('font-size') == 11
        assert view.session.codex is None  # Existing tabs retain their transport.
        new = app.window.new_tab()
        assert new.session.codex == '/bin/sh'
        assert mac_key('<Meta><Shift>e') == (14, 768)
        assert mac_key('F5') == (96, 0)
        with tempfile.TemporaryDirectory() as directory:
            settings = load_settings(isolated=True)
            # Exercise GNOME schema sharing with the memory backend.
            source = __import__('errand.ui', fromlist=['Gio']).Gio.SettingsSchemaSource.new_from_directory(
                str(Path(__file__).resolve().parent.parent / 'schemas'), None, False)
            schema = source.lookup('org.gnome.shell.extensions.errand', False)
            from errand.ui import Gio
            settings.gnome = Gio.Settings.new_full(schema, None, None)
            settings.save({'font-size': 17, 'codex-path': '/bin/sh', 'open-errand': '<Super>F5'})
            other = Gio.Settings.new_full(schema, None, None)
            assert other.get_int('font-size') == 17
            assert other.get_string('codex-path') == '/bin/sh'
            assert other.get_strv('open-errand') == ['<Super>F5']
        print('PASS: Tab focus, portable add button, preferences validation, new-tab path and GNOME sharing')
    except Exception as exc:
        failures.append(exc)
    finally:
        app.quit()


app.connect('activate', run)
app.run([])
if failures:
    raise failures[0]
