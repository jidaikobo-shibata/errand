#!/usr/bin/env python3
"""Message navigation checks without real Codex or persistent settings."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib

root = Path(__file__).resolve().parent.parent
app = Application(isolated=True, command=[sys.executable, str(root / 'tests/fake_server.py')])
failures = []


def populate(_):
    view = app.window.current
    assert all(not button.get_sensitive() for button in view.navigation_buttons.values())
    for index in range(8):
        view.add_message('あなた' if index % 2 == 0 else 'Codex', ('発言%d\n' % index) * 12)
    view.add_message('エラー', '操作ログ')
    GLib.timeout_add(100, verify)


def verify():
    try:
        view = app.window.current
        assert len(view.message_widgets) == 8
        navigation = view.navigation_buttons['previous'].get_parent()
        assert navigation.get_parent().get_first_child() is navigation
        assert navigation.get_next_sibling() is view.status_scroll
        assert view.navigation_index() == 7
        view.navigation_buttons['previous'].grab_focus()
        view.navigation_buttons['previous'].emit('clicked')
        assert view.navigation_index() == 6
        assert view.message_widgets[6].has_css_class('errand-navigation-target')
        assert .45 <= view.message_widgets[6].get_opacity() <= .55
        assert not view.message_widgets[6].get_child().has_css_class('errand-navigation-target')
        assert view.navigation_buttons['previous'].has_focus()
        assert not view._follow_bottom
        view.navigate_messages('first')
        assert view.navigation_index() == 0
        assert not view.message_widgets[6].has_css_class('errand-navigation-target')
        assert view.message_widgets[6].get_opacity() == 1
        assert view.message_widgets[0].has_css_class('errand-navigation-target')
        assert view.scroll.get_vadjustment().get_value() == 0
        assert not view.navigation_buttons['previous'].get_sensitive()
        view.navigate_messages('next')
        assert view.navigation_index() == 1
        old_value = view.scroll.get_vadjustment().get_value()
        assert old_value > 0
        view.add_message('Codex', '受信中\n' * 15)
        view.scroll_bottom()
        assert view.navigation_index() == 1
        assert view.scroll.get_vadjustment().get_value() == old_value
        for _ in range(10):
            view.navigate_messages('next')
        assert view.navigation_index() == 8
        assert not view.navigation_buttons['next'].get_sensitive()
        view.navigate_messages('previous')
        assert view.navigation_index() == 7
        view.navigate_messages('latest')
        view.scroll_bottom()
        assert view._follow_bottom
        adjustment = view.scroll.get_vadjustment()
        assert abs(adjustment.get_value() - max(0, adjustment.get_upper() - adjustment.get_page_size())) < 1
        assert not view.navigation_buttons['latest'].get_sensitive()
        assert view.message_widgets[-1].has_css_class('errand-navigation-target')
        other = app.window.new_tab()
        assert not other.message_widgets
        assert all(not button.get_sensitive() for button in other.navigation_buttons.values())
        app.highlight_test_view = view
        GLib.timeout_add(120, verify_visible)
        GLib.timeout_add(850, verify_clear)
    except Exception as exc:
        failures.append(exc)
        app.quit()
    return False


def verify_visible():
    try:
        view = app.highlight_test_view
        assert .45 <= view.message_widgets[-1].get_opacity() <= .55
        assert view._highlight_timeout is not None
    except Exception as exc:
        failures.append(exc)
        app.quit()
    return False


def verify_clear():
    try:
        view = app.highlight_test_view
        assert view._highlight_timeout is None
        assert not any(widget.has_css_class('errand-navigation-target') for widget in view.message_widgets)
        assert all(widget.get_opacity() == 1 for widget in view.message_widgets)
        view.highlight_message(0)
        view.close()
        assert view._highlight_timeout is None
        assert not view.message_widgets[0].has_css_class('errand-navigation-target')
        assert view.message_widgets[0].get_opacity() == 1
        print('PASS: navigation, controller order, focus retention, temporary highlight and cleanup')
    except Exception as exc:
        failures.append(exc)
    finally:
        app.quit()
    return False


app.connect('activate', populate)
app.run([])
if failures:
    raise failures[0]
