#!/usr/bin/env python3
"""Offline bookmark UI checks; use a private Wayland display."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib, Gtk

app = Application(smoke=True, isolated=True)
errors = []

def check():
    try:
        view = app.window.current
        menu = view.bookmark_button
        key = app.bookmarks.save('納品前チェック', '報告書を確認してください。')
        menu.refresh()
        menu.popup()
        assert menu.menu.get_visible()
        assert app.window.key_controller.emit('key-pressed', 65307, 0, 0)
        assert not menu.menu.get_visible()
        menu.choose(app.bookmarks.items[0])
        assert app.window.tabs.get_n_pages() == 1
        assert menu.prompt() == '報告書を確認してください。'
        assert view.session.thread_id is None and not view.session.busy
        menu.choose(app.bookmarks.items[0])
        assert app.window.tabs.get_n_pages() == 2
        assert menu.prompt() == '報告書を確認してください。'
        target = app.window.current
        target.input.get_buffer().set_text('')
        target.attachments['/fixture'] = object()
        target.bookmark_button.choose(app.bookmarks.items[0])
        assert app.window.tabs.get_n_pages() == 3
        assert '/fixture' in target.attachments
        target.attachments.clear()
        new = app.window.current.bookmark_button
        dialog, name, editor, save, error = new.edit(text='翻訳してください。')
        save.emit('clicked')
        assert error.get_text() and len(app.bookmarks.items) == 1
        name.set_text('翻訳')
        save.emit('clicked')
        assert len(app.bookmarks.items) == 2
        assert app.bookmarks.items[-1]['text'] == '翻訳してください。'
        dialog, name, editor, save, error = new.edit(app.bookmarks.items[0])
        name.set_text('納品前の確認')
        save.emit('clicked')
        assert app.bookmarks.items[0]['id'] == key
        assert app.bookmarks.items[0]['name'] == '納品前の確認'
        manager = new.manage()
        assert manager.get_visible()
        def find_delete(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == '削除':
                return widget
            child = widget.get_first_child()
            while child:
                found = find_delete(child)
                if found:
                    return found
                child = child.get_next_sibling()
        delete = find_delete(manager)
        assert delete is not None
        delete.emit('clicked')
        alert = manager.get_visible_dialog()
        assert alert is not None
        alert.emit('response', 'cancel')
        alert.close()
        assert len(app.bookmarks.items) == 2
        delete.emit('clicked')
        manager.get_visible_dialog().emit('response', 'delete')
        assert len(app.bookmarks.items) == 1
        assert app.bookmarks.items[0]['name'] == '翻訳'
        manager.close()
        assert all(not c.session.busy and c.session.thread_id is None for c in app.window.conversations)
        print('PASS: bookmark menu, no submission, draft/attachment preservation, validation, edit and management')
    except Exception as exc:
        import traceback
        traceback.print_exc()
        errors.append(exc)
    finally:
        app.quit()
    return False

app.connect('activate', lambda _: GLib.idle_add(check))
app.run([])
raise SystemExit(bool(errors))
