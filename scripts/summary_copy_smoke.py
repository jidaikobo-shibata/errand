#!/usr/bin/env python3
"""Summary generation is clipboard-free; fresh summaries are copied from status."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib

root = Path(__file__).resolve().parent.parent
app = Application(isolated=True, command=[sys.executable, str(root / 'tests/fake_server.py')])
errors = []
copied = []
state = 0
deadline = time.monotonic() + 15

def tick():
    global state
    try:
        if time.monotonic() > deadline:
            raise TimeoutError('summary UI did not complete')
        view = app.window.current
        if state == 0:
            if view.models_loading or not view.model_entries:
                return True
            view.get_clipboard = lambda: type('Clipboard', (), {'set': lambda _, text: copied.append(text)})()
            view.add_message('あなた', '依頼')
            view.add_message('Codex', '回答', markdown=True)
            view.update_model_controls()
            assert not view.summary_copy_button.get_sensitive()
            assert not view.summary_copy_button.get_visible()
            view.summary_button.emit('clicked')
            assert view.session.busy and not view.summary_copy_button.get_sensitive()
            state = 1
        elif state == 1:
            if view.session.busy or not view.summary_text:
                return True
            assert not copied
            assert '要約しました' in view.status.get_text()
            assert view.summary_copy_button.get_sensitive()
            assert view.summary_copy_button.get_visible()
            assert view.summary_copy_button.get_parent() is view.status_row
            original = view.summary_text
            other = app.window.new_tab()
            assert other.summary_text is None
            assert not other.summary_copy_button.get_visible()
            assert view.summary_text == original
            view.summary_copy_button.emit('clicked')
            assert copied == [original]
            assert view.summary_text is None
            assert not view.summary_copy_button.get_visible()
            view.copy_summary(None)
            assert copied == [original]
            assert not view.session.busy
            view.event('summary', {'text': '更新前の要約'})
            assert view.summary_copy_button.get_visible()
            view.event('delta', {'item_id': 'later', 'text': '新しい回答'})
            assert view.summary_text is None
            assert not view.summary_copy_button.get_sensitive()
            assert not view.summary_copy_button.get_visible()
            view.copy_summary(None)
            assert len(copied) == 1
            view.event('summary', {'text': '別の要約'})
            view.event('reset', {})
            assert view.summary_text is None and not view.summary_copy_button.get_sensitive()
            app.quit()
            print('PASS: clipboard-free generation, status copy, consumed-summary hiding, tab isolation and stale-summary invalidation')
            return False
        return True
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        app.quit()
        return False

app.connect('activate', lambda _: GLib.timeout_add(50, tick))
app.run([])
raise SystemExit(bool(errors))
