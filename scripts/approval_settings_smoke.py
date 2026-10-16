#!/usr/bin/env python3
"""Offline approval-default persistence and tab isolation checks."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib

app = Application(smoke=True, isolated=True)
failures = []

def check():
    try:
        first = app.window.current
        assert first.session.approvals_reviewer == 'user'
        second = app.window.new_tab()
        first.approval_choice.set_selected(1)
        assert first.session.approvals_reviewer == 'auto_review'
        assert app.settings.get('approval-reviewer') == 'auto_review'
        assert second.session.approvals_reviewer == 'user'
        third = app.window.new_tab()
        assert third.session.approvals_reviewer == 'auto_review'
        assert third.approval_choice.get_selected() == 1
        app.save_preferences({'font-size': 15, 'codex-path': '', 'open-errand': '', 'shortcuts': {}})
        assert app.settings.get('approval-reviewer') == 'auto_review'
        with patch.object(app.settings, 'set_approval_reviewer', side_effect=OSError('write denied')):
            third.approval_choice.set_selected(0)
        assert third.session.approvals_reviewer == 'auto_review'
        assert third.approval_choice.get_selected() == 1
        assert app.settings.get('approval-reviewer') == 'auto_review'
        assert 'write denied' in third.status.get_text()
        third.session._busy = True
        third.approval_choice.set_selected(0)
        assert third.session.approvals_reviewer == 'auto_review'
        assert third.approval_choice.get_selected() == 1
        third.session._busy = False
        third.approval_choice.set_selected(0)
        assert app.settings.get('approval-reviewer') == 'user'
        assert first.session.approvals_reviewer == 'auto_review'
        fourth = app.window.new_tab()
        assert fourth.session.approvals_reviewer == 'user'
        print('PASS: approval defaults, tab isolation, preferences preservation and failed-change rollback')
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failures.append(exc)
    finally:
        app.quit()
    return False

app.connect('activate', lambda _: GLib.idle_add(check))
app.run([])
raise SystemExit(bool(failures))
