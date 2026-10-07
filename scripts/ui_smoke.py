#!/usr/bin/env python3
"""Exercise the real GTK widgets with a fake transport, without model calls."""
from pathlib import Path
import json
import sys
import time
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Adw, Gdk, Gio, GLib, Gtk
from errand.markdown_widget import CodeBlock, TableBlock

root = Path(__file__).resolve().parent.parent
app = Application(isolated=True, command=[sys.executable, str(root / "tests/fake_server.py")])
stage = 0
deadline = time.monotonic() + 15
failure = []
tab_checks = {}

def tick():
    global stage
    try:
        window = app.window.current
        if time.monotonic() > deadline:
            raise RuntimeError(f"UI smoke timeout, stage {stage}")
        if stage == 0:
            if window.models_loading or not window.model_entries:
                return True
            window.input.grab_focus()
            if not window.input.has_focus():
                return True
            assert [entry["model"] for entry in window.model_entries] == ["fake-fast", "fake-steady", "fake-no-effort"]
            assert window.selected_model()["model"] == "fake-fast"
            assert window.selected_effort() == "low"
            assert window.effort_choice.get_sensitive()
            window.model_choice.set_selected(1)
            assert window.selected_effort() == "medium"
            window.model_choice.set_selected(0)
            assert window.selected_effort() == "high"
            window.effort_choice.set_selected(0)
            assert window.selected_effort() == "low"
            window.input.grab_focus()
            buffer = window.input.get_buffer()
            buffer.set_text("先頭\n途中\n末尾")
            page = app.window.tabs.get_selected_page()
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Up, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == 0
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Down, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == buffer.get_char_count()
            assert not (app.window.tabs.get_shortcuts() &
                        (Adw.TabViewShortcuts.CONTROL_HOME | Adw.TabViewShortcuts.CONTROL_END))
            assert app.window.tabs.get_shortcuts() & Adw.TabViewShortcuts.CONTROL_PAGE_UP
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Home, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == 0
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_End, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == buffer.get_char_count()
            assert app.window.tabs.get_selected_page() is page
            window.send_button.grab_focus()
            assert not app.window.key_controller.emit("key-pressed", Gdk.KEY_Up, 0, Gdk.ModifierType.CONTROL_MASK)
            window.input.grab_focus()
            buffer.set_text("approval")
            assert app.window.key_controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
            assert not app.window.key_controller.emit("key-pressed", Gdk.KEY_Return, 0, Gdk.ModifierType(0))
            assert not window.session.busy
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Return, 0, Gdk.ModifierType.CONTROL_MASK)
            assert window.session.busy
            stage = 1
        elif stage == 1 and window.requests:
            if not window.requests["approval-1"].get_last_child().get_first_child().has_focus():
                return True
            assert not window.model_choice.get_sensitive()
            assert not window.effort_choice.get_sensitive()
            window.session.answer("approval-1", {"decision": "decline"})
            stage = 2
        elif stage == 2 and not window.session.busy and window.messages:
            assert not window.requests
            assert window.selected_model()["model"] == "fake-fast"
            assert window.selected_effort() == "low"
            assert window.model_choice.get_sensitive()
            assert window.model_button.get_sensitive()
            window.model_button.popup()
            window.model_choice.set_selected(1)
            assert window.selected_effort() == "medium"
            assert "fake-steady" in window.model_button.get_label()
            window.model_popover.popdown()
            window.input.get_buffer().set_text("question")
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_KP_Enter, 0, Gdk.ModifierType.CONTROL_MASK)
            stage = 3
        elif stage == 3 and window.requests:
            window.session.answer("question-1", {"answers": {"name": {"answers": ["test"]}}})
            stage = 4
        elif stage == 4 and not window.session.busy and len(window.messages) == 2:
            window.reset(None)
            stage = 5
        elif stage == 5:
            assert window.history.get_first_child() is None
            assert not hasattr(window, "folder_button")
            assert not hasattr(window, "write")
            assert window.model_choice.get_sensitive()
            window.model_choice.set_selected(1)
            assert window.effort_values == ["medium"]
            assert window.selected_effort() == "medium"
            window.model_choice.set_selected(2)
            assert window.selected_effort() is None
            assert not window.effort_choice.get_sensitive()
            window.model_choice.set_selected(0)
            assert window.selected_model()["model"] == "fake-fast"
            source = "# 見出し\n\n- **太字**と[リンク](https://example.com/?a=1&b=2)\n```bash\necho '<b>&</b>'\n```"
            window.event("user", {"text": "**利用者の入力はそのまま**"})
            window.event("delta", {"item_id": "markdown-test", "text": source[:30]})
            window.event("delta", {"item_id": "markdown-test", "text": source[30:]})
            content = window.messages["markdown-test"]
            assert content.get_source() == source
            content.set_source(content.get_source(), immediate=True)
            assert "• 太字とリンク" in content.get_text()
            assert "echo '<b>&</b>'" in content.get_text()
            assert '<a href="https://example.com/?a=1&amp;b=2">' in content.get_label()
            assert "weight=\"bold\"" in content.get_label()
            assert "**利用者の入力はそのまま**" in window.conversation_text()
            assert source in window.conversation_text()
            window.event("assistant", {"item_id": "markdown-test", "text": source + "\n完了"})
            assert content.get_source() == source + "\n完了"
            assert content.get_text().endswith("完了")
            code = next(part for part in content._parts if isinstance(part, CodeBlock))
            assert isinstance(code, Gtk.Frame)
            copied = []
            code.copy_to_clipboard = copied.append
            code.copy_button.emit("clicked")
            assert copied == ["echo '<b>&</b>'\n"]
            assert code.copy_button.get_label() == "コピーしました"
            code.copy_button.grab_focus()
            content.set_source(source + "\n追記", immediate=True)
            assert code in content._parts
            assert code.copy_button.get_label() == "コピーしました"
            streamed = "```python\n  print('<&>')"
            content.set_source(streamed, immediate=True)
            code = content._parts[0]
            assert isinstance(code, CodeBlock)
            assert code.copy_button.get_sensitive()
            code.copy_to_clipboard = copied.append
            code.copy_button.emit("clicked")
            assert copied[-1] == "  print('<&>')"
            content.set_source(streamed + "\n```\n\n~~~text\nsecond\n~~~", immediate=True)
            assert content._parts[0] is code
            assert code.copy_button.get_sensitive()
            for part in content._parts:
                if isinstance(part, CodeBlock):
                    part.copy_to_clipboard = copied.append
                    part.copy_button.emit("clicked")
            assert copied[-2:] == ["  print('<&>')\n", "second\n"]
            window.approval_choice.set_selected(1)
            assert window.session.approvals_reviewer == "auto_review"
            assert window.approval_choice.get_selected() == 1
            assert "自動" not in window.model_button.get_label()
            window.input.get_buffer().set_text("permissions")
            window.send(None)
            stage = 6
        elif stage == 6 and "permissions-1" in window.requests:
            if not window.requests["permissions-1"].get_last_child().get_first_child().has_focus():
                return True
            assert not window.approval_choice.get_sensitive()
            request = window.requests["permissions-1"]
            reason = request.get_first_child().get_next_sibling()
            assert reason.get_text() == "検証出力の作成"
            details = reason.get_next_sibling()
            assert isinstance(details, Gtk.Expander) and not details.get_expanded()
            details.set_expanded(True)
            detail_content = details.get_child().get_child()
            if isinstance(detail_content, Gtk.Viewport):
                detail_content = detail_content.get_child()
            assert detail_content.get_first_child().get_text() == "作業場所"
            row = request.get_last_child()
            row.get_first_child().get_next_sibling().emit("clicked")
            stage = 7
        elif stage == 7 and not window.session.busy and not window.requests:
            response = json.loads(window.messages["answer-1"].get_source())
            assert response["result"] == {"permissions": {"fileSystem": {"write": ["/tmp/test-output.pdf"]}}, "scope": "session"}
            window.input.get_buffer().set_text("permissions")
            window.send(None)
            stage = 8
        elif stage == 8 and "permissions-1" in window.requests:
            row = window.requests["permissions-1"].get_last_child()
            row.get_last_child().emit("clicked")
            stage = 9
        elif stage == 9 and not window.session.busy and not window.requests:
            response = json.loads(window.messages["answer-2"].get_source())
            assert response["result"] == {"permissions": {}, "scope": "turn"}
            window.event("delta", {"item_id": "scroll-test", "text": "質問です。\n\n" + "前の回答\n" * 60 + "\n1. あり\n2. なし"})
            stage = 10
        elif stage == 10:
            content = window.messages["scroll-test"]
            if content._pending is not None:
                return True
            adjustment = window.scroll.get_vadjustment()
            assert adjustment.get_upper() > adjustment.get_page_size()
            assert abs(adjustment.get_value() - (adjustment.get_upper() - adjustment.get_page_size())) < 1
            # A final reply and status update resize both content and viewport.
            window.event("assistant", {"item_id": "scroll-test", "text": content.get_source() + "\n\n最後の選択肢\n" * 10})
            window.status.set_text("状態表示\n" * 3)
            stage = 11
        elif stage == 11:
            adjustment = window.scroll.get_vadjustment()
            assert abs(adjustment.get_value() - (adjustment.get_upper() - adjustment.get_page_size())) < 1
            # Reproduce GTK restoring an earlier position after changed.
            adjustment.emit("changed")
            adjustment.set_value(adjustment.get_value() - 30)
            stage = 12
        elif stage == 12:
            adjustment = window.scroll.get_vadjustment()
            assert abs(adjustment.get_value() - (adjustment.get_upper() - adjustment.get_page_size())) < 1
            assert not window.stop.get_visible()
            window.model_button.popup()
            assert window.model_popover.get_autohide()
            app.window.outside_click(None, 1, 20, 20)
            assert not window.model_popover.get_visible()
            window.model_button.popup()
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
            assert not window.model_popover.get_visible()
            assert app.window.get_visible()
            tab_checks["first"] = window
            tab_checks["text"] = window.conversation_text()
            window.input.get_buffer().set_text("残す下書き")
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_t, 0, Gdk.ModifierType.CONTROL_MASK)
            assert app.window.tabs.get_n_pages() == 2
            assert app.window.current is not window
            stage = 13
        elif stage == 13:
            if window.models_loading or not window.model_entries:
                return True
            window.input.grab_focus()
            if not window.input.has_focus():
                return True
            tab_checks["second"] = window
            assert window.history.get_first_child() is None
            assert window.session is not tab_checks["first"].session
            assert window.session.approvals_reviewer == "user"
            assert tab_checks["first"].session.approvals_reviewer == "auto_review"
            buffer = window.input.get_buffer()
            buffer.set_text("先頭\n末尾")
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Home, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == 0
            assert app.window.current is window
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_End, 0, Gdk.ModifierType.CONTROL_MASK)
            assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == buffer.get_char_count()
            assert app.window.current is window
            window.input.get_buffer().set_text("approval")
            window.send(None)
            stage = 14
        elif stage == 14 and window.requests:
            assert window.stop.get_visible()
            assert window.page.get_loading()
            assert window.page.get_needs_attention()
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_q, 0, Gdk.ModifierType.CONTROL_MASK)
            assert app.window.close_dialog is not None
            assert app.window.close_dialog.get_default_response() == "cancel"
            app.window.close_dialog.close()
            stage = 17
        elif stage == 17:
            if app.window.close_dialog is not None:
                return True
            assert window.session.busy
            app.window.tabs.set_selected_page(tab_checks["first"].page)
            first = tab_checks["first"]
            assert first.conversation_text() == tab_checks["text"]
            assert first.input.get_buffer().get_text(first.input.get_buffer().get_start_iter(), first.input.get_buffer().get_end_iter(), False) == "残す下書き"
            app.window.hide_on_close()
            assert tab_checks["second"].session.busy
            app.window.present()
            app.window.tabs.set_selected_page(tab_checks["second"].page)
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_w, 0, Gdk.ModifierType.CONTROL_MASK)
            assert app.window.close_dialog is not None
            app.window.close_dialog.close()  # Closing the dialog defaults to cancel.
            stage = 15
        elif stage == 15:
            if app.window.close_dialog is not None:
                return True
            second = tab_checks["second"]
            assert not second.closed and second.session.busy
            assert app.window.tabs.get_n_pages() == 2
            app.window.tabs.close_page(second.page)
            # Exercise the actual dialog response, retaining its safe default.
            dialog = app.window.close_dialog
            dialog.set_close_response("close")
            dialog.close()
            stage = 16
        elif stage == 16:
            if app.window.close_dialog is not None:
                return True
            second = tab_checks["second"]
            assert second.closed and second.session._closed
            assert app.window.tabs.get_n_pages() == 1
            first = tab_checks["first"]
            assert not first.closed
            assert first.conversation_text() == tab_checks["text"]
            # Closing the last idle tab leaves a fresh, empty tab.
            app.window.tabs.set_selected_page(first.page)
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_w, 0, Gdk.ModifierType.CONTROL_MASK)
            assert first.closed
            assert app.window.tabs.get_n_pages() == 1
            assert app.window.current.history.get_first_child() is None
            assert app.window.current is not first
            window = app.window.current
            long_url = "https://example.com/" + "token" * 300
            window.add_message("あなた", long_url)
            source = "| 項目 | 内容 |\n| --- | --- |\n| **URL** | " + long_url + " |\n| 安全 | <script>& |"
            table_view = window.add_message("Codex", source, markdown=True)
            table = next(part for part in table_view._parts if isinstance(part, TableBlock))
            assert table.cells[2].get_text() == "URL"
            assert table.cells[-1].get_text() == "<script>&"
            assert source in window.conversation_text()
            tab_checks["table"] = table
            stage = 18
        elif stage == 18:
            table = tab_checks["table"]
            assert app.window.get_width() <= 680
            assert table.get_width() <= window.scroll.get_width()
            user_box = window.history.get_first_child()
            assert user_box.get_width() <= window.scroll.get_width()
            assert user_box.get_last_child().get_wrap_mode().value_nick == "word-char"
            assert user_box.get_last_child().get_layout().get_line_count() > 1
            if window.models_loading or not window.model_entries:
                return True
            tab_checks["drop_dir"] = tempfile.TemporaryDirectory(prefix="errand-drop-test-")
            first = Path(tab_checks["drop_dir"].name) / "報告書 one.xlsx"
            second = Path(tab_checks["drop_dir"].name) / "資料.pdf"
            first.write_bytes(b"fixture, not a real spreadsheet")
            second.write_bytes(b"fixture, not a real PDF")
            tab_checks["dropped"] = [str(first), str(second)]
            files = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(first)), Gio.File.new_for_path(str(second))])
            assert window.file_drop.emit("drop", files, 0., 0.)
            assert window.file_drop.emit("drop", files, 0., 0.)
            assert list(window.attachments) == tab_checks["dropped"]
            assert window.session.thread_id is None  # Drop alone sends nothing.
            window.attachments[str(first)].get_last_child().emit("clicked")
            assert list(window.attachments) == [str(second)]
            assert window.file_drop.emit("drop", files, 0., 0.)
            remote = Gdk.FileList.new_from_list([Gio.File.new_for_uri("https://example.com/report.xlsx")])
            assert not window.file_drop.emit("drop", remote, 0., 0.)
            assert len(window.attachments) == 2
            window.send(None)  # Missing request must retain the targets.
            assert not window.session.busy and len(window.attachments) == 2
            other = app.window.new_tab()
            assert not other.attachments and len(window.attachments) == 2
            app.window.tabs.close_page(other.page)
            app.window.tabs.set_selected_page(window.page)
            window.input.get_buffer().set_text("対象を確認してください")
            tab_checks["initialize"] = window.session._initialize
            def fail_initialize(server):
                raise RuntimeError("test connection failure")
            window.session._initialize = fail_initialize
            window.send(None)
            assert len(window.attachments) == 2
            stage = 19
        elif stage == 19 and not window.session.busy:
            buffer = window.input.get_buffer()
            assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == "対象を確認してください"
            assert len(window.attachments) == 2
            window.session._initialize = tab_checks["initialize"]
            window.send(None)
            assert len(window.attachments) == 2
            buffer.set_text("次のお願いの下書き")
            stage = 20
        elif stage == 20 and not window.session.busy:
            assert not window.attachments
            assert not window.attachment_scroll.get_visible()
            buffer = window.input.get_buffer()
            assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == "次のお願いの下書き"
            text = window.conversation_text()
            assert all(path in text for path in tab_checks["dropped"])
            assert "JSON配列" in text
            tab_checks["summary_source"] = text
            window.conversation_popover.popup()
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
            assert not window.conversation_popover.get_visible()
            assert app.window.get_visible()
            tab_checks["copied"] = []
            window.get_clipboard = lambda: type("Clipboard", (), {"set": lambda _, text: tab_checks["copied"].append(text)})()
            window.copy_button.emit("clicked")
            assert tab_checks["copied"] == [text]
            assert window.summary_button.get_sensitive()
            window.summary_button.emit("clicked")
            stage = 21
        elif stage == 21 and not window.session.busy:
            assert len(tab_checks["copied"]) == 2
            assert tab_checks["copied"][1].startswith("以下は前の会話からの引き継ぎです。")
            assert window.conversation_text() == tab_checks["summary_source"]
            assert window.summary_button.get_sensitive()
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_q, 0, Gdk.ModifierType.CONTROL_MASK)
            print("PASS: GTK tabs, approvals, file drops and conversation/summary copy", flush=True)
            return False
        return True
    except Exception as error:
        failure.append(error)
        app.quit()
        return False

app.connect("activate", lambda _: GLib.timeout_add(50, tick))
app.run([sys.argv[0]])
if "drop_dir" in tab_checks:
    tab_checks["drop_dir"].cleanup()
if failure:
    raise failure[0]
