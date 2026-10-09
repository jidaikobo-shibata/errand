#!/usr/bin/env python3
"""Check per-tab prompt recall, acceptance and draft preservation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gdk, GLib, Gtk

app = Application(isolated=True, smoke=True)
failures = []


def verify(_):
    try:
        view = app.window.current
        buffer = view.input.get_buffer()
        keys = app.window.key_controller
        def press(key):
            return keys.emit('key-pressed', key, 0, Gdk.ModifierType(0))
        def text():
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        view.input.grab_focus()
        assert not press(Gdk.KEY_Up)
        for draft in ('最初のお願い\n二行目', '次のお願い'):
            buffer.set_text(draft)
            sent = draft + '\n対象ファイル（検証用）'
            view.pending_submission = (draft, {}, sent)
            view.event('submitted', {'text': sent})
        assert view.prompt_history == ['最初のお願い\n二行目', '次のお願い']
        assert not text()
        assert press(Gdk.KEY_Up) and text() == '次のお願い'
        assert press(Gdk.KEY_Up) and text() == '最初のお願い\n二行目'
        assert press(Gdk.KEY_Up) and text() == '最初のお願い\n二行目'
        assert press(Gdk.KEY_Down) and text() == '次のお願い'
        assert press(Gdk.KEY_Down) and text() == ''
        assert not press(Gdk.KEY_Down)
        assert press(Gdk.KEY_Up)
        buffer.insert_at_cursor('を編集', -1)
        assert not press(Gdk.KEY_Up)
        assert text() == '次のお願いを編集'
        buffer.set_text('書きかけ')
        assert not press(Gdk.KEY_Up)
        # An accepted older submission must not overwrite a newer draft.
        view.pending_submission = ('送ったお願い', {}, '送ったお願い')
        view.event('submitted', {'text': '送ったお願い'})
        assert text() == '書きかけ'
        buffer.set_text('')
        assert press(Gdk.KEY_Up) and text() == '送ったお願い'
        # Failed/unacknowledged submissions are not history entries.
        view.pending_submission = ('失敗したお願い', {}, '失敗したお願い')
        view.event('error', {'message': '検証エラー'})
        assert '失敗したお願い' not in view.prompt_history
        other = app.window.new_tab()
        assert not other.prompt_history
        assert view.prompt_history
        view.event('reset', {})
        assert not view.prompt_history
        print('PASS: accepted prompt history, multiline recall, empty return, editing, draft preservation and tab isolation')
    except Exception as exc:
        failures.append(exc)
    finally:
        app.quit()


app.connect('activate', lambda _: GLib.timeout_add(60, lambda: verify(app)))
app.run([])
if failures:
    raise failures[0]
