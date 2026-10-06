#!/usr/bin/env python3
"""Exercise the real GTK widgets with a fake transport, without model calls."""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gdk, GLib, Gtk
from errand.markdown_widget import CodeBlock

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
            window.input.get_buffer().set_text("approval")
            window.input.grab_focus()
            assert app.window.key_controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
            assert not app.window.key_controller.emit("key-pressed", Gdk.KEY_Return, 0, Gdk.ModifierType(0))
            assert not window.session.busy
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_Return, 0, Gdk.ModifierType.CONTROL_MASK)
            assert window.session.busy
            stage = 1
        elif stage == 1 and window.requests:
            assert window.requests["approval-1"].get_last_child().get_first_child().has_focus()
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
            assert window.requests["permissions-1"].get_last_child().get_first_child().has_focus()
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
            tab_checks["second"] = window
            assert window.history.get_first_child() is None
            assert window.session is not tab_checks["first"].session
            assert window.session.approvals_reviewer == "user"
            assert tab_checks["first"].session.approvals_reviewer == "auto_review"
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
            assert app.window.key_controller.emit("key-pressed", Gdk.KEY_q, 0, Gdk.ModifierType.CONTROL_MASK)
            print("PASS: GTK tabs, Ctrl+Q, isolated sessions, busy close/cancel, hide, models, Markdown and scroll", flush=True)
            return False
        return True
    except Exception as error:
        failure.append(error)
        app.quit()
        return False

app.connect("activate", lambda _: GLib.timeout_add(50, tick))
app.run([sys.argv[0]])
if failure:
    raise failure[0]
