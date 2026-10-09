#!/usr/bin/env python3
"""Check compact status layout and bounded scrolling using fake transport."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib, Gtk
root = Path(__file__).resolve().parent.parent
app = Application(isolated=True, command=[sys.executable, str(root / 'tests/fake_server.py')])
failures = []
stage = 0


def verify():
    global stage
    try:
        view = app.window.current
        if stage == 0:
            view.status.set_text('入力を待っています。')
            stage = 1
        elif stage in (1, 3):
            assert view.status_scroll.get_height() <= 36, view.status_scroll.get_height()
            assert view.navigation_buttons['first'].get_parent().get_height() <= 28
            assert view.status_scroll.get_policy()[1] == Gtk.PolicyType.NEVER
            if stage == 3:
                print('PASS: compact idle status and navigation, long-log scrolling, shrinking after completion')
                app.quit()
                return False
            view.status.set_text('実行コマンド\n' * 100)
            stage = 2
        elif stage == 2:
            scroll = view.status_scroll
            assert scroll.get_height() <= 160, scroll.get_height()
            assert scroll.get_policy()[1] == Gtk.PolicyType.AUTOMATIC
            adjustment = scroll.get_vadjustment()
            assert adjustment.get_upper() > adjustment.get_page_size()
            adjustment.set_value(100)
            assert adjustment.get_value() == 100
            view.status.set_text('入力を待っています。')
            stage = 3
    except Exception as exc:
        failures.append(exc)
        app.quit()
        return False
    return True


app.connect('activate', lambda _: GLib.timeout_add(150, verify))
app.run([])
if failures:
    raise failures[0]
