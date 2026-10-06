"""Small, keyboard-accessible GTK window for disposable conversations."""

import argparse
import json
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .session import Session
from .markdown_widget import MarkdownView


def label(text):
    widget = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    return widget


def status_label(text=""):
    widget = label(text)
    widget.set_visible(bool(text))
    widget.connect("notify::label", lambda item, _: item.set_visible(bool(item.get_label())))
    return widget


def effort_title(value):
    names = {"none": "なし", "minimal": "最小", "low": "低", "medium": "中",
             "high": "高", "xhigh": "とても高", "max": "最大", "ultra": "最高"}
    return f"{value}（{names[value]}）" if value in names else value


class Conversation(Gtk.Box):
    def __init__(self, owner, app, codex):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_start=16, margin_end=16, margin_bottom=16)
        self.owner = owner
        self.page = None
        self.closed = False
        self.topic = "新しいお願い"
        self.messages = {}
        self.requests = {}
        self.session = Session(self.event, dispatch=GLib.idle_add, codex=codex, command=app.command)
        self.model_entries = []
        self.effort_values = [None]
        self.models_loading = False
        self.updating_selectors = False
        body = self
        selection = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        model_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.model_choice = Gtk.DropDown.new_from_strings([])
        self.model_choice.set_sensitive(False)
        self.model_choice.set_enable_search(True)
        self.model_choice.update_property([Gtk.AccessibleProperty.LABEL], ["モデル"])
        self.model_choice.connect("notify::selected", self.model_changed)
        model_box.append(self.model_choice)
        selection.append(model_box)
        effort_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.effort_choice = Gtk.DropDown.new_from_strings([])
        self.effort_choice.set_sensitive(False)
        self.effort_choice.update_property([Gtk.AccessibleProperty.LABEL], ["推論の強さ"])
        self.effort_choice.connect("notify::selected", self.effort_changed)
        effort_box.append(self.effort_choice)
        selection.append(effort_box)
        self.model_status = status_label("現在のモデルと推論の強さを取得中…")
        selection.append(self.model_status)
        self.approval_choice = Gtk.DropDown.new_from_strings(["自分で承認", "自動レビュー"])
        self.approval_choice.update_property([Gtk.AccessibleProperty.LABEL], ["このタブの承認方法"])
        self.approval_choice.connect("notify::selected", self.approval_changed)
        self.approval_choice.set_tooltip_text("このタブの承認方法。自動レビューはApprove for meに相当し、全許可ではありません。")
        self.model_popover = Gtk.Popover(autohide=True)
        self.model_popover.set_child(selection)
        self.model_button = Gtk.MenuButton(label="モデル取得中…", tooltip_text="モデルと推論の強さを変更")
        self.model_button.set_popover(self.model_popover)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=220)
        self._scroll_idle = None
        # GTK can restore the old value after emitting changed during layout.
        # Defer moving to the bottom until that allocation has finished.
        self.scroll.get_vadjustment().connect("changed", lambda *_: self.queue_scroll_bottom())
        self.history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.scroll.set_child(self.history)
        body.append(self.scroll)
        self.request_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.append(self.request_box)
        self.status = status_label()
        self.status.add_css_class("errand-status")
        self.status.update_property([Gtk.AccessibleProperty.LABEL], ["実行状況"])
        body.append(self.status)
        input_scroll = Gtk.ScrolledWindow(min_content_height=100, max_content_height=140)
        input_scroll.add_css_class("errand-input")
        self.input_css = Gtk.CssProvider()
        self.input_css.load_from_data(b"""
            .errand-message {
                padding: 8px 0;
            }
            .errand-message-user {
                background-color: @window_fg_color;
                color: @window_bg_color;
                border-radius: 20px;
                padding: 10px 16px;
            }
            .errand-message-codex {
                background-color: transparent;
            }
            .errand-approval {
                background-color: alpha(@window_fg_color, 0.07);
                border: 1px solid alpha(@window_fg_color, 0.20);
                border-radius: 12px;
                padding: 12px;
            }
            .errand-status {
                color: alpha(@window_fg_color, 0.75);
                background-color: alpha(@window_fg_color, 0.05);
                border-left: 3px solid alpha(@window_fg_color, 0.25);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 0.9em;
            }
            .errand-input {
                background-color: @view_bg_color;
                border: 1px solid alpha(@window_fg_color, 0.30);
                border-radius: 16px;
                padding: 10px;
            }
            .errand-input textview,
            .errand-input textview text {
                background-color: transparent;
            }
            .errand-input.editing {
                border-color: alpha(@window_fg_color, 0.65);
                box-shadow: 0 0 0 1px alpha(@window_fg_color, 0.15);
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self.input_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.input.update_property([Gtk.AccessibleProperty.LABEL], ["お願い"])
        input_focus = Gtk.EventControllerFocus()
        input_focus.connect("enter", lambda _: input_scroll.add_css_class("editing"))
        input_focus.connect("leave", lambda _: input_scroll.remove_css_class("editing"))
        self.input.add_controller(input_focus)
        input_scroll.set_child(self.input)
        body.append(input_scroll)
        row = Gtk.Box(spacing=8)
        self.send_button = Gtk.Button(label="送信", sensitive=False)
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("clicked", self.send)
        row.append(self.send_button)
        self.stop = Gtk.Button(label="中断", sensitive=False, visible=False)
        self.stop.connect("clicked", lambda _: self.session.interrupt())
        row.append(self.stop)
        copy = Gtk.Button(label="会話をコピー")
        copy.connect("clicked", self.copy)
        row.append(copy)
        row.append(Gtk.Box(hexpand=True))
        row.append(self.model_button)
        row.append(self.approval_choice)
        body.append(row)
        if not app.smoke:
            self.session.load_models()

    def close(self):
        self.closed = True
        if self._scroll_idle is not None:
            GLib.source_remove(self._scroll_idle)
            self._scroll_idle = None
        self.model_popover.popdown()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.input_css)
        self.session.close()

    def focus_input(self):
        if self.owner.current is self:
            self.input.grab_focus()

    def send(self, _):
        buffer = self.input.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            entry = self.selected_model()
            if entry is None:
                raise RuntimeError("モデルを取得して選択してから送信してください。")
            self.session.send(text,
                              model=entry["model"] if entry else None, effort=self.selected_effort())
            self.model_popover.popdown()
            buffer.set_text("")
        except Exception as error:
            self.status.set_text(str(error))

    def reset(self, _):
        try:
            self.session.reset()
        except Exception as error:
            self.status.set_text(str(error))

    def selected_model(self):
        index = self.model_choice.get_selected()
        return self.model_entries[index] if index < len(self.model_entries) else None

    def selected_effort(self):
        index = self.effort_choice.get_selected()
        return self.effort_values[index] if index < len(self.effort_values) else None

    def update_model_controls(self):
        locked = self.session.busy
        self.approval_choice.set_sensitive(not locked)
        self.model_button.set_sensitive(not locked)
        if locked:
            self.model_popover.popdown()
        self.model_choice.set_sensitive(not locked and not self.models_loading and bool(self.model_entries))
        self.effort_choice.set_sensitive(not locked and not self.models_loading and
                                         self.selected_model() is not None and any(self.effort_values))
        self.send_button.set_sensitive(not self.session.busy and self.selected_model() is not None)
        self.update_model_label()

    def update_model_label(self):
        entry = self.selected_model()
        if entry is None:
            self.model_button.set_label("モデル取得中…" if self.models_loading else "モデルを選択")
            return
        name = entry["model"]
        if name.startswith("gpt-"):
            name = "GPT-" + name[4:].title()
        effort = self.selected_effort()
        self.model_button.set_label(name + (" · " + effort if effort else ""))
        self.model_button.update_property([Gtk.AccessibleProperty.LABEL],
                                         ["モデルと推論の強さを変更: " + self.model_button.get_label()])

    def set_efforts(self, entry, preferred=None):
        options = entry.get("supportedReasoningEfforts", []) if entry else []
        self.effort_values = [option["reasoningEffort"] for option in options] or [None]
        names = [effort_title(value) for value in self.effort_values if value is not None]
        if not names:
            names = ["設定なし"] if entry else []
        self.effort_choice.set_model(Gtk.StringList.new(names))
        default = preferred if preferred in self.effort_values else entry.get("defaultReasoningEffort") if entry else None
        index = self.effort_values.index(default) if default in self.effort_values else 0
        self.effort_choice.set_selected(index)
        self.effort_changed(None, None)

    def model_changed(self, *_):
        if self.updating_selectors:
            return
        entry = self.selected_model()
        self.model_choice.set_tooltip_text(entry.get("description", "") if entry else "モデルを取得してください。")
        self.set_efforts(entry)
        self.update_model_controls()

    def effort_changed(self, *_):
        entry = self.selected_model()
        effort = self.selected_effort()
        options = entry.get("supportedReasoningEfforts", []) if entry else []
        description = next((option.get("description", "") for option in options
                            if option["reasoningEffort"] == effort), "")
        self.effort_choice.set_tooltip_text(description or "モデルを選ぶと、対応する強さを選べます。")
        self.update_model_label()

    def approval_changed(self, *_):
        try:
            self.session.set_approval_reviewer("auto_review" if self.approval_choice.get_selected() == 1 else "user")
            self.update_model_label()
        except Exception as error:
            self.status.set_text(str(error))

    def approval_details(self, box, params):
        reason = params.get("reason")
        if reason:
            box.append(label(str(reason)))
        else:
            box.append(label(str(params.get("command") or params.get("grantRoot") or "操作の理由は提供されていません。")))
        expander = Gtk.Expander(label="詳細", expanded=False)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for key, title in (("command", "実行コマンド"), ("cwd", "作業場所"),
                           ("grantRoot", "書き込み許可の対象"), ("permissions", "要求された権限"),
                           ("networkApprovalContext", "接続先"), ("item", "操作内容")):
            value = params.get(key)
            if value:
                details.append(label(title))
                details.append(label(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)))
        detail_scroll = Gtk.ScrolledWindow(max_content_height=180, propagate_natural_height=True)
        detail_scroll.set_child(details)
        expander.set_child(detail_scroll)
        box.append(expander)

    def add_message(self, heading, text, markdown=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("errand-message")
        if heading == "あなた":
            box.add_css_class("errand-message-user")
            box.set_halign(Gtk.Align.END)
            box.set_margin_start(48)
        elif heading == "Codex":
            box.add_css_class("errand-message-codex")
        title = label(heading)
        title.add_css_class("caption")
        if heading in {"あなた", "Codex"}:
            title.set_visible(False)
            box.update_property([Gtk.AccessibleProperty.LABEL], [heading + "の発言"])
        box.append(title)
        content = MarkdownView(text) if markdown else label(text)
        if markdown:
            content.connect("rendered", lambda *_: self.queue_scroll_bottom())
        box.append(content)
        self.history.append(box)
        self.queue_scroll_bottom()
        return content

    def queue_scroll_bottom(self):
        if self._scroll_idle is None:
            self._scroll_idle = GLib.idle_add(self.scroll_bottom)

    def scroll_bottom(self):
        self._scroll_idle = None
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def event(self, kind, data):
        if self.closed:
            return
        if kind == "models_state":
            self.models_loading = data["loading"]
            if self.models_loading:
                self.model_status.set_text("モデル一覧を取得中…")
            self.update_model_controls()
        elif kind == "models":
            previous = self.selected_model()
            previous_model = previous["model"] if previous else None
            previous_effort = self.selected_effort()
            self.updating_selectors = True
            self.model_entries = data["models"]
            names = [entry["model"] for entry in data["models"]]
            self.model_choice.set_model(Gtk.StringList.new(names))
            index = next((i for i, entry in enumerate(self.model_entries)
                          if entry["model"] == (previous_model or data.get("default_model"))), 0)
            self.model_choice.set_selected(index)
            self.set_efforts(self.selected_model(), previous_effort if previous else data.get("default_effort"))
            self.updating_selectors = False
            self.model_status.set_text("" if data["models"]
                                       else "選択できるモデルがありません。新しいタブでお試しください。")
            self.update_model_controls()
        elif kind == "models_error":
            self.model_status.set_text("モデル一覧を取得できませんでした: " + data["message"] + " 新しいタブでお試しください。")
        elif kind == "thread":
            self.update_model_controls()
        elif kind == "user":
            if self.topic == "新しいお願い":
                self.topic = " ".join(data["text"].split())[:24]
                if self.page:
                    self.page.set_title(self.topic)
                    self.page.set_tooltip(data["text"][:200])
            self.add_message("あなた", data["text"])
        elif kind in {"delta", "assistant"}:
            item_id = data["item_id"]
            content = self.messages.get(item_id)
            if content is None:
                content = self.add_message("Codex", "", markdown=True)
                self.messages[item_id] = content
            content.set_source(content.get_source() + data["text"] if kind == "delta" else data["text"],
                               immediate=kind == "assistant")
            self.queue_scroll_bottom()
        elif kind == "progress":
            item = data["item"]
            description = item.get("command") or item.get("type", "作業")
            self.status.set_text(("完了: " if data["completed"] else "実行: ") + description)
        elif kind == "error":
            self.add_message("エラー", data["message"])
        elif kind == "state":
            busy = data["busy"]
            self.status.set_text(data["message"])
            self.send_button.set_sensitive(not busy)
            self.stop.set_sensitive(busy)
            self.stop.set_visible(busy)
            if self.page:
                self.page.set_loading(busy)
                self.page.set_needs_attention(bool(self.requests))
            self.update_model_controls()
        elif kind == "request":
            self.show_request(data)
        elif kind == "request_resolved":
            self.remove_request(data["request_id"])
        elif kind == "requests_clear":
            for request_id in list(self.requests):
                self.remove_request(request_id)
        elif kind == "reset":
            self.focus_input()
            while (child := self.history.get_first_child()) is not None:
                self.history.remove(child)
            self.messages.clear()
            for request_id in list(self.requests):
                self.remove_request(request_id)
            self.status.set_text("")
            self.update_model_controls()
            self.focus_input()

    def remove_request(self, request_id):
        widget = self.requests.pop(request_id, None)
        if widget:
            self.focus_input()
            self.request_box.remove(widget)
            if self.page:
                self.page.set_needs_attention(bool(self.requests))

    def show_request(self, data):
        request_id, method, params = data["request_id"], data["method"], data["params"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if method != "item/tool/requestUserInput":
            box.add_css_class("errand-approval")
        self.requests[request_id] = box
        if self.page:
            self.page.set_needs_attention(True)
        self.request_box.append(box)
        def respond(result):
            try:
                self.session.answer(request_id, result)
            except Exception as error:
                self.status.set_text(str(error))
        if method == "item/tool/requestUserInput":
            box.append(label("Codexからの質問"))
            entries = []
            for question in params["questions"]:
                box.append(label(question["question"]))
                options = question.get("options") or []
                for option in options:
                    box.append(label(option["label"] + ": " + option["description"]))
                entry = Gtk.Entry(visibility=not question.get("isSecret", False),
                                  placeholder_text="選択肢名または回答を入力")
                entry.update_property([Gtk.AccessibleProperty.LABEL], [question["question"]])
                box.append(entry)
                entries.append((question["id"], entry))
            button = Gtk.Button(label="回答を送る")
            def submit(_):
                if any(not entry.get_text().strip() for _, entry in entries):
                    self.status.set_text("すべての質問に回答してください。")
                    return
                respond({"answers": {qid: {"answers": [entry.get_text()]} for qid, entry in entries}})
            button.connect("clicked", submit)
            box.append(button)
        elif method == "item/permissions/requestApproval":
            box.append(label("要求された権限を許可しますか？"))
            self.approval_details(box, params)
            row = Gtk.Box(spacing=8)
            allow = Gtk.Button(label="今回の作業だけ許可")
            allow.connect("clicked", lambda _: respond({"permissions": params.get("permissions", {}), "scope": "turn"}))
            row.append(allow)
            session_allow = Gtk.Button(label="このタブで許可")
            session_allow.set_tooltip_text("要求された権限だけを、このタブを閉じるまで許可します。")
            session_allow.connect("clicked", lambda _: respond({"permissions": params.get("permissions", {}), "scope": "session"}))
            row.append(session_allow)
            deny = Gtk.Button(label="拒否")
            deny.connect("clicked", lambda _: respond({"permissions": {}, "scope": "turn"}))
            row.append(deny)
            box.append(row)
        else:
            box.append(label("この操作を今回だけ許可しますか？"))
            self.approval_details(box, params)
            row = Gtk.Box(spacing=8)
            for title, decision in [("今回だけ許可", "accept"), ("拒否", "decline"), ("作業を中止", "cancel")]:
                button = Gtk.Button(label=title)
                button.connect("clicked", lambda _, d=decision: respond({"decision": d}))
                row.append(button)
            box.append(row)
        GLib.idle_add(self.focus_request)

    def focus_request(self):
        if not self.closed and self.owner.current is self and self.requests:
            box = next(reversed(self.requests.values()))
            target = box.get_last_child()
            if isinstance(target, Gtk.Box):
                target = target.get_first_child()
            target.grab_focus()
        return False

    def conversation_text(self):
        texts = []
        child = self.history.get_first_child()
        while child:
            heading = child.get_first_child()
            content = heading.get_next_sibling()
            text = content.get_source() if isinstance(content, MarkdownView) else content.get_text()
            texts.append(heading.get_text() + "\n" + text)
            child = child.get_next_sibling()
        return "\n\n".join(texts)

    def copy(self, _):
        self.get_clipboard().set(self.conversation_text())


class Window(Adw.ApplicationWindow):
    def __init__(self, app, codex):
        super().__init__(application=app, title="Errand — 小さなお願い",
                         default_width=640, default_height=740)
        self.app, self.codex = app, codex
        self.conversations = []
        self.close_dialog = None
        self.connect("close-request", self.hide_on_close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.set_content(root)
        header = Adw.HeaderBar()
        root.append(header)
        self.new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="新しいタブ（Ctrl+T）")
        self.new.update_property([Gtk.AccessibleProperty.LABEL], ["新しいタブ"])
        self.new.connect("clicked", lambda _: self.new_tab())
        header.pack_start(self.new)
        self.tabs = Adw.TabView(vexpand=True)
        self.tabs.connect("close-page", self.close_page)
        self.tabs.connect("notify::selected-page", self.selected_tab)
        bar = Adw.TabBar(view=self.tabs, autohide=False)
        bar.set_tooltip_text("タブを閉じると会話を終了します。閉じた会話は復元できません。")
        root.append(bar)
        root.append(self.tabs)
        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self.key)
        self.add_controller(self.key_controller)
        self.click_controller = Gtk.GestureClick(button=0)
        self.click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.click_controller.connect("pressed", self.outside_click)
        self.add_controller(self.click_controller)
        self.new_tab()

    @property
    def current(self):
        page = self.tabs.get_selected_page()
        return page.get_child() if page else None

    def new_tab(self):
        conversation = Conversation(self, self.app, self.codex)
        self.conversations.append(conversation)
        page = self.tabs.append(conversation)
        conversation.page = page
        page.set_title(conversation.topic)
        page.set_tooltip("タブを閉じると、この会話を終了します（復元できません）。")
        self.tabs.set_selected_page(page)
        return conversation

    def selected_tab(self, *_):
        if self.current:
            if self.current.requests:
                GLib.idle_add(self.current.focus_request)
            else:
                self.current.focus_input()

    def hide_on_close(self, *_):
        self.set_visible(False)
        return True

    def key(self, controller, keyval, keycode, state):
        if self.current and self.current.model_popover.get_visible():
            if keyval == Gdk.KEY_Escape:
                self.current.model_popover.popdown()
                return True
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if modifiers == Gdk.ModifierType.CONTROL_MASK and self.close_dialog is None:
            if keyval in (Gdk.KEY_q, Gdk.KEY_Q):
                self.quit_application()
                return True
            if keyval in (Gdk.KEY_t, Gdk.KEY_T):
                self.new_tab()
                return True
            if keyval in (Gdk.KEY_w, Gdk.KEY_W):
                page = self.tabs.get_selected_page()
                if page:
                    self.tabs.close_page(page)
                return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            if self.current and self.close_dialog is None:
                self.current.send(None)
            return True
        if keyval == Gdk.KEY_Escape and self.close_dialog is None:
            self.set_visible(False)
            return True
        return False

    def outside_click(self, gesture, count, x, y):
        if not self.current or not self.current.model_popover.get_visible():
            return
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None:
            if target in (self.current.model_popover, self.current.model_button):
                return
            target = target.get_parent()
        self.current.model_popover.popdown()

    def quit_application(self):
        if not any(conversation.session.busy for conversation in self.conversations):
            self.app.quit()
            return
        dialog = Adw.AlertDialog(heading="作業を停止してErrandを終了しますか？",
                                 body="すべてのタブの作業を停止します。会話と下書きは復元できません。")
        dialog.add_response("cancel", "戻る")
        dialog.add_response("quit", "停止して終了")
        dialog.set_response_appearance("quit", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self.close_dialog = dialog
        def respond(_, response):
            self.close_dialog = None
            if response == "quit":
                self.app.quit()
        dialog.connect("response", respond)
        dialog.present(self)

    def close_page(self, view, page):
        conversation = page.get_child()
        if not conversation.session.busy:
            self.finish_close(page, True)
            return True
        dialog = Adw.AlertDialog(heading="作業を中断して閉じますか？",
                                 body="このタブの作業を停止し、会話を終了します。閉じた会話は復元できません。")
        dialog.add_response("cancel", "戻る")
        dialog.add_response("close", "中断して閉じる")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        self.close_dialog = dialog
        def respond(_, response):
            self.close_dialog = None
            self.finish_close(page, response == "close")
        dialog.connect("response", respond)
        dialog.present(self)
        return True

    def finish_close(self, page, close):
        if close:
            conversation = page.get_child()
            conversation.close()
            self.conversations.remove(conversation)
        self.tabs.close_page_finish(page, close)
        if close and self.tabs.get_n_pages() == 0:
            self.new_tab()

    def shutdown_sessions(self):
        for conversation in list(self.conversations):
            conversation.close()
        self.conversations.clear()


class Application(Adw.Application):
    def __init__(self, codex=None, smoke=False, isolated=False, command=None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if smoke or isolated else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id="jp.jidaikobo.Errand", flags=flags)
        self.codex, self.smoke = codex, smoke
        self.command = command
        self.window = None
        self.connect("activate", self.activate_window)
        self.connect("shutdown", self.shutdown_session)

    def activate_window(self, _):
        if self.window is None:
            self.window = Window(self, self.codex)
            self.hold()
        self.window.present()
        if self.smoke:
            GLib.timeout_add(250, lambda: (self.quit(), False)[1])

    def shutdown_session(self, _):
        if self.window:
            self.window.shutdown_sessions()


def main():
    parser = argparse.ArgumentParser(description="Errand — 小さなCodexチャット")
    parser.add_argument("--codex", help="Codex実行ファイルの絶対パス")
    parser.add_argument("--smoke-test", action="store_true", help="表示後に終了（Codexへは接続しない）")
    args = parser.parse_args()
    if not Gtk.init_check():
        parser.exit(1, "GNOMEの画面へ接続できません。デスクトップのTerminalから起動してください。\n")
    return Application(args.codex, args.smoke_test).run([sys.argv[0]])
