"""Small, keyboard-accessible GTK window for disposable conversations."""

import argparse
import json
import random
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .session import Session
from .login import Login
from .runtime import RuntimeSetup, SETUP_DESCRIPTION
from .markdown_widget import MarkdownView
from .preferences import Preferences, load_settings, mac_key
from .settings import DEFAULTS, validate
from .shortcuts import ACTIONS, bindings, key_signature, signature, validate_shortcuts
from .bookmarks import Bookmarks
from .bookmark_widget import BookmarkMenu


STARTUP_TIPS = (
    "ファイルをドロップして、お願いの対象にできます。",
    "計算や、計算の説明を頼めます。",
    "翻訳や文章の推敲を頼めます。",
    "Codexに設定済みのスキルを使えます。",
    "調べものを頼めます。",
    "端末内のファイル探しを頼めます。",
)


def label(text):
    widget = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
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
        self.app = app
        self.page = None
        self.closed = False
        self.topic = "新しいお願い"
        self.messages = {}
        self.message_widgets = []
        self._navigation_index = None
        self._highlight_widget = None
        self._highlight_timeout = None
        self.requests = {}
        self.attachments = {}
        self.pending_submission = None
        self.prompt_history = []
        self._prompt_history_index = None
        self._recalling_prompt = False
        self.session = Session(self.event, dispatch=GLib.idle_add, codex=codex, command=app.command)
        self.login = Login(self.login_event, dispatch=GLib.idle_add, codex=codex, command=app.command)
        self.login_url = None
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
        self.model_pointer = Gtk.GestureClick(button=0)
        self.model_pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.model_pointer.connect("pressed", self.model_pointer_pressed)
        self.model_popover.add_controller(self.model_pointer)
        self.model_popover.set_child(selection)
        self.model_button = Gtk.MenuButton(label="モデル取得中…", tooltip_text="モデルと推論の強さを変更")
        self.model_button.set_popover(self.model_popover)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=220,
                                        hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._scroll_idle = None
        self._follow_bottom = True
        self._scroll_setting = False
        self._scroll_layout = False
        # GTK can restore the old value after emitting changed during layout.
        # Defer moving to the bottom until that allocation has finished.
        self.scroll.get_vadjustment().connect("changed", self.scroll_layout_changed)
        self.scroll.get_vadjustment().connect("value-changed", self.scroll_position_changed)
        self.scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        self.scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.scroll_controller.connect("scroll", self.user_scroll)
        self.scroll.add_controller(self.scroll_controller)
        self.history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.scroll.set_child(self.history)
        body.append(self.scroll)
        self.request_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.append(self.request_box)
        self.status = status_label(random.choice(STARTUP_TIPS))
        self.status.add_css_class("errand-status")
        self.status.update_property([Gtk.AccessibleProperty.LABEL], ["実行状況"])
        self.status_scroll = Gtk.ScrolledWindow(
            max_content_height=160, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.status_scroll.set_child(self.status)
        self.status_scroll.set_visible(self.status.get_visible())
        self.status.connect("notify::visible", lambda item, _: self.status_scroll.set_visible(item.get_visible()))
        self.status.connect("notify::label", lambda *_: self.update_status_size())
        status_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        body.append(status_area)
        navigation = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        navigation.add_css_class("errand-navigation")
        self.navigation_buttons = {}
        for action, title, shortcut in (("first", "最初の発言へ", "Ctrl+Alt+Shift+↑"),
                                       ("previous", "前の発言へ", "Ctrl+Alt+↑"),
                                       ("next", "次の発言へ", "Ctrl+Alt+↓"),
                                       ("latest", "最新へ移動して自動追従", "Ctrl+Alt+Shift+↓")):
            button = Gtk.Button(tooltip_text=f"{title}（{shortcut}）", sensitive=False)
            button.add_css_class("flat")
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            icon = Gtk.DrawingArea(width_request=16, height_request=16)
            def draw_arrow(widget, context, width, height, direction=action):
                color = widget.get_color()
                context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
                context.set_line_width(1.5)
                upward = direction in {"first", "previous"}
                # Two concentric chevrons, like a rotated 《 / 》, share one footprint.
                offsets = (0, 4) if direction in {"first", "latest"} else (2,)
                for offset in offsets:
                    edge = 11 - offset if upward else 5 + offset
                    tip = edge - 5 if upward else edge + 5
                    context.move_to(3, edge)
                    context.line_to(width / 2, tip)
                    context.line_to(width - 3, edge)
                context.stroke()
            icon.set_draw_func(draw_arrow)
            button.set_child(icon)
            button.connect("clicked", lambda _, direction=action: self.navigate_messages(direction))
            navigation.append(button)
            self.navigation_buttons[action] = button
        status_area.append(navigation)
        status_area.append(self.status_scroll)
        self.setup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.setup_description = label(SETUP_DESCRIPTION)
        self.setup_box.append(self.setup_description)
        self.setup_progress = Gtk.ProgressBar(show_text=True, visible=False)
        self.setup_progress.update_property([Gtk.AccessibleProperty.LABEL], ["動作環境の準備"])
        self.setup_box.append(self.setup_progress)
        setup_buttons = Gtk.Box(spacing=8)
        self.setup_button = Gtk.Button(label="動作環境を準備する")
        self.setup_button.add_css_class("suggested-action")
        self.setup_button.connect("clicked", lambda _: app.runtime.start())
        setup_buttons.append(self.setup_button)
        self.setup_cancel_button = Gtk.Button(label="準備を中止", visible=False)
        self.setup_cancel_button.connect("clicked", lambda _: self.cancel_setup())
        setup_buttons.append(self.setup_cancel_button)
        self.setup_box.append(setup_buttons)
        body.append(self.setup_box)
        self.login_box = Gtk.Box(spacing=8, visible=False)
        self.login_button = Gtk.Button(label="ChatGPTにログイン")
        self.login_button.connect("clicked", lambda _: self.login.start())
        self.login_box.append(self.login_button)
        self.login_browser_button = Gtk.Button(label="ブラウザを開く", visible=False)
        self.login_browser_button.connect("clicked", lambda _: self.open_login_browser())
        self.login_box.append(self.login_browser_button)
        self.login_cancel_button = Gtk.Button(label="ログインを中止", visible=False)
        self.login_cancel_button.connect("clicked", lambda _: self.login.cancel())
        self.login_box.append(self.login_cancel_button)
        body.append(self.login_box)
        input_scroll = Gtk.ScrolledWindow(min_content_height=100, max_content_height=140)
        input_scroll.add_css_class("errand-input")
        self.input_css = Gtk.CssProvider()
        self.input_css.load_from_data(b"""
            .errand-navigation button {
                min-height: 20px;
                min-width: 24px;
                padding: 2px 4px;
            }
            .errand-table-cell {
                padding: 8px;
                border-bottom: 1px solid alpha(@window_fg_color, 0.15);
                border-right: 1px solid alpha(@window_fg_color, 0.10);
            }
            .errand-table-header {
                background-color: alpha(@window_fg_color, 0.06);
                font-weight: bold;
            }
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
                padding: 3px 10px;
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
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.input.get_buffer().connect("changed", self.prompt_edited)
        self.input.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                   ["お願い", STARTUP_TIPS[0]])
        input_focus = Gtk.EventControllerFocus()
        input_focus.connect("enter", lambda _: input_scroll.add_css_class("editing"))
        input_focus.connect("leave", lambda _: input_scroll.remove_css_class("editing"))
        self.input.add_controller(input_focus)
        input_scroll.set_child(self.input)
        self.attachment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.attachment_scroll = Gtk.ScrolledWindow(max_content_height=100,
                                                    propagate_natural_height=True,
                                                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                    visible=False)
        self.attachment_scroll.set_child(self.attachment_box)
        body.append(self.attachment_scroll)
        body.append(input_scroll)
        self.file_drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        self.file_drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.file_drop.connect("drop", self.drop_files)
        self.add_controller(self.file_drop)
        row = Gtk.Box(spacing=8)
        self.send_button = Gtk.Button(label="送信", sensitive=False)
        self.send_button.add_css_class("suggested-action")
        self.send_button.connect("clicked", self.send)
        row.append(self.send_button)
        self.bookmark_button = BookmarkMenu(self)
        row.append(self.bookmark_button)
        self.stop = Gtk.Button(label="中断", sensitive=False, visible=False)
        self.stop.connect("clicked", lambda _: self.session.interrupt())
        row.append(self.stop)
        self.conversation_menu = Gtk.MenuButton(label="会話について")
        self.conversation_popover = Gtk.Popover()
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.copy_button = Gtk.Button(label="会話全文をコピー")
        self.copy_button.connect("clicked", self.copy)
        menu.append(self.copy_button)
        self.summary_button = Gtk.Button(label="引き継ぎ用の要約をコピー", sensitive=False)
        self.summary_button.connect("clicked", self.copy_summary)
        menu.append(self.summary_button)
        self.conversation_popover.set_child(menu)
        self.conversation_menu.set_popover(self.conversation_popover)
        row.append(self.conversation_menu)
        row.append(Gtk.Box(hexpand=True))
        row.append(self.model_button)
        row.append(self.approval_choice)
        body.append(row)
        app.runtime.subscribe(self.setup_event)
        if not app.smoke:
            self.session.load_models()

    def model_pointer_pressed(self, controller, count, x, y):
        # Use popover-local bounds here; window picking uses different
        # coordinates and can mistake a dropdown click for an outside click.
        width, height = self.model_popover.get_width(), self.model_popover.get_height()
        if not self.model_popover.get_visible() or width <= 0 or height <= 0:
            return
        if not (0 <= x < width and 0 <= y < height):
            self.model_popover.popdown()
            GLib.idle_add(self.focus_input)

    def close(self):
        self.closed = True
        self.clear_navigation_highlight()
        self.app.runtime.unsubscribe(self.setup_event)
        self.bookmark_button.menu.popdown()
        self.login.close()
        if self._scroll_idle is not None:
            GLib.source_remove(self._scroll_idle)
            self._scroll_idle = None
        self.model_popover.popdown()
        self.conversation_popover.popdown()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.input_css)
        self.session.close(wait=False)

    def cancel_setup(self):
        self.status.set_text("動作環境の準備を中止しています…")
        self.setup_cancel_button.set_sensitive(False)
        self.app.runtime.cancel()

    def setup_event(self, kind, data):
        if self.closed or not self.setup_box.get_visible():
            return
        if kind == "state":
            self.setup_button.set_sensitive(not data["active"])
            self.setup_cancel_button.set_visible(data["active"])
            if data["active"]:
                self.setup_cancel_button.set_sensitive(True)
                self.setup_progress.set_visible(True)
                self.status.set_text("動作環境を確認しています…")
        elif kind == "progress":
            fraction = data["fraction"]
            self.setup_progress.set_visible(True)
            self.setup_progress.set_fraction(fraction or 0)
            if fraction is None:
                self.setup_progress.pulse()
            self.setup_progress.set_text(data["message"] + (f"（{fraction:.0%}）" if fraction is not None else ""))
            self.status.set_text(self.setup_progress.get_text())
        elif kind == "ready":
            self.session.codex = self.login.codex = data["path"]
            self.setup_box.set_visible(False)
            self.status.set_text("動作環境の準備が完了しました。ログイン状態を確認しています…")
            self.session.load_models()
        elif kind == "error":
            self.setup_progress.set_visible(False)
            self.status.set_text(data["message"])
        elif kind == "cancelled":
            self.setup_progress.set_visible(False)
            self.status.set_text("動作環境の準備を中止しました。ボタンからもう一度準備できます。")

    def open_login_browser(self):
        if self.login_url:
            try:
                Gio.AppInfo.launch_default_for_uri(self.login_url, None)
            except Exception:
                self.status.set_text("ブラウザを開けませんでした。「ブラウザを開く」で再試行してください。")

    def login_event(self, kind, data):
        if self.closed:
            return
        if kind == "state":
            self.login_button.set_sensitive(not data["active"])
            self.login_cancel_button.set_visible(data["active"])
            if data["active"]:
                self.status.set_text("ログイン画面を準備しています…")
            else:
                self.login_url = None
                self.login_browser_button.set_visible(False)
                if data.get("cancelled"):
                    self.status.set_text("ログインを中止しました。必要なときにもう一度ログインできます。")
        elif kind == "browser":
            self.login_url = data["url"]
            self.login_browser_button.set_visible(True)
            self.status.set_text("ブラウザでログインを完了してください。終わったらこの画面へ戻ってください。")
            self.open_login_browser()
        elif kind == "success":
            self.login_box.set_visible(False)
            self.status.set_text("ログインできました。モデル一覧を取得しています…")
            self.session.load_models()
        elif kind == "error":
            self.status.set_text(data["message"])

    def focus_input(self):
        if self.owner.current is self:
            self.input.grab_focus()

    def prompt_edited(self, *_):
        if not self._recalling_prompt:
            self._prompt_history_index = None

    def recall_prompt(self, direction):
        buffer = self.input.get_buffer()
        if not self.prompt_history or (self._prompt_history_index is None and buffer.get_char_count()):
            return False
        if self._prompt_history_index is None:
            if direction > 0:
                return False
            index = len(self.prompt_history)
        else:
            index = self._prompt_history_index
        index = max(0, min(len(self.prompt_history), index + direction))
        self._recalling_prompt = True
        try:
            buffer.set_text(self.prompt_history[index] if index < len(self.prompt_history) else "")
            buffer.place_cursor(buffer.get_end_iter())
            self.input.scroll_mark_onscreen(buffer.get_insert())
        finally:
            self._recalling_prompt = False
        self._prompt_history_index = index if index < len(self.prompt_history) else None
        return True

    def drop_files(self, target, file_list, x, y):
        if self.closed:
            return False
        try:
            paths = []
            for file in file_list.get_files():
                path = file.get_path()
                if path is None and sys.platform == "darwin":
                    uri = file.get_uri()
                    # GTK 4.22's macOS backend escapes the scheme's colon.
                    # Correct that prefix only; let Gio decode the path once.
                    if uri.startswith("file%3A///"):
                        path = Gio.File.new_for_uri("file:" + uri[len("file%3A"):]).get_path()
                if path is None or not Path(path).is_file():
                    raise ValueError("ローカルのファイルをドロップしてください。")
                paths.append(str(Path(path).absolute()))
            if not paths:
                return False
            for path in paths:
                if path in self.attachments:
                    continue
                row = Gtk.Box(spacing=8)
                name = Gtk.Label(label=Path(path).name, xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.MIDDLE)
                name.set_max_width_chars(48)
                name.set_tooltip_text(path)
                name.update_property([Gtk.AccessibleProperty.LABEL], ["対象ファイル: " + path])
                row.append(name)
                remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="対象から外す")
                remove.update_property([Gtk.AccessibleProperty.LABEL], [Path(path).name + "を対象から外す"])
                remove.connect("clicked", lambda _, value=path: self.remove_attachment(value))
                row.append(remove)
                self.attachments[path] = row
                self.attachment_box.append(row)
            self.attachment_scroll.set_visible(bool(self.attachments))
            self.focus_input()
            return True
        except Exception as error:
            self.status.set_text(str(error))
            return False

    def remove_attachment(self, path):
        row = self.attachments.pop(path, None)
        if row:
            self.attachment_box.remove(row)
        self.attachment_scroll.set_visible(bool(self.attachments))

    def clear_attachments(self):
        for path in list(self.attachments):
            self.remove_attachment(path)

    def send(self, _):
        buffer = self.input.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            if self.session.busy:
                raise RuntimeError("現在のお願いが終わるまでお待ちください。")
            if not text.strip():
                raise ValueError("ファイルについてのお願いを入力してください。" if self.attachments else "お願いを入力してください。")
            entry = self.selected_model()
            if entry is None:
                raise RuntimeError("モデルを取得して選択してから送信してください。")
            draft = text
            attachments = dict(self.attachments)
            if self.attachments:
                text += "\n\n対象ファイル（ローカルの絶対パス、JSON配列）:\n" + json.dumps(list(self.attachments), ensure_ascii=False)
            self.session.send(text,
                              model=entry["model"] if entry else None, effort=self.selected_effort())
            self.pending_submission = (draft, attachments, text.strip())
            self.model_popover.popdown()
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
        self.summary_button.set_sensitive(not self.session.busy and self.selected_model() is not None
                                          and bool(self.conversation_text()))
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
        if heading in {"あなた", "Codex"}:
            row = Gtk.Overlay()
            row.add_css_class("errand-message-row")
            row.set_child(box)
            self.history.append(row)
            self.message_widgets.append(row)
        else:
            self.history.append(box)
        self.queue_scroll_bottom()
        self.update_navigation()
        return content

    def message_positions(self):
        positions = []
        for widget in self.message_widgets:
            valid, bounds = widget.compute_bounds(self.history)
            positions.append(bounds.get_y() if valid else 0)
        return positions

    def update_status_size(self):
        width = self.status_scroll.get_width() or max(200, self.owner.get_width() - 32)
        _, natural, _, _ = self.status.measure(Gtk.Orientation.VERTICAL, width)
        policy = Gtk.PolicyType.AUTOMATIC if natural > 160 else Gtk.PolicyType.NEVER
        self.status_scroll.set_policy(Gtk.PolicyType.NEVER, policy)

    def navigation_index(self):
        if not self.message_widgets:
            return None
        if self._navigation_index is not None:
            return min(self._navigation_index, len(self.message_widgets) - 1)
        if self._follow_bottom:
            return len(self.message_widgets) - 1
        value = self.scroll.get_vadjustment().get_value()
        return max((index for index, y in enumerate(self.message_positions()) if y <= value + 2), default=0)

    def update_navigation(self):
        if not hasattr(self, "navigation_buttons"):
            return
        index = self.navigation_index()
        adjustment = self.scroll.get_vadjustment()
        at_bottom = adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() <= 2
        enabled = {"first": index is not None and (index > 0 or adjustment.get_value() > 2),
                   "previous": index is not None and index > 0,
                   "next": index is not None and index < len(self.message_widgets) - 1,
                   "latest": index is not None and (not self._follow_bottom or not at_bottom)}
        for action, button in self.navigation_buttons.items():
            button.set_sensitive(enabled[action])

    def navigate_messages(self, action):
        index = self.navigation_index()
        if index is None:
            return
        if action == "latest":
            self.highlight_message(len(self.message_widgets) - 1)
            self._navigation_index = None
            self._follow_bottom = True
            self.queue_scroll_bottom()
            self.update_navigation()
            return
        target = 0 if action == "first" else max(0, min(len(self.message_widgets) - 1,
                                                       index + (-1 if action == "previous" else 1)))
        self._follow_bottom = False
        self._navigation_index = target
        self._scroll_setting = True
        try:
            adjustment = self.scroll.get_vadjustment()
            bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
            adjustment.set_value(min(bottom, self.message_positions()[target]))
        finally:
            self._scroll_setting = False
        self.highlight_message(target)
        self.update_navigation()

    def clear_navigation_highlight(self):
        if self._highlight_timeout is not None:
            GLib.source_remove(self._highlight_timeout)
            self._highlight_timeout = None
        if self._highlight_widget is not None:
            self._highlight_widget.set_opacity(1)
            self._highlight_widget.remove_css_class("errand-navigation-target")
            self._highlight_widget = None

    def highlight_message(self, index):
        self.clear_navigation_highlight()
        self._highlight_widget = self.message_widgets[index]
        self._highlight_widget.add_css_class("errand-navigation-target")
        self._highlight_widget.set_opacity(.5)
        started = GLib.get_monotonic_time()
        def restore():
            elapsed = GLib.get_monotonic_time() - started
            # Hold the cue long enough to see it after the scroll has rendered.
            progress = max(0, min(1, (elapsed - 200_000) / 600_000))
            if progress >= 1:
                self._highlight_timeout = None
                self.clear_navigation_highlight()
                return False
            eased = progress * progress * (3 - 2 * progress)
            self._highlight_widget.set_opacity(.5 + .5 * eased)
            return True
        self._highlight_timeout = GLib.timeout_add(16, restore)

    def queue_scroll_bottom(self):
        if (self._follow_bottom or self._scroll_layout) and self._scroll_idle is None:
            self._scroll_idle = GLib.idle_add(self.scroll_bottom)

    def user_scroll(self, controller, dx, dy):
        if dy < 0:
            self._follow_bottom = False
        self._navigation_index = None
        if dy > 0:
            adjustment = self.scroll.get_vadjustment()
            if adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() <= 2:
                self._follow_bottom = True
                self.queue_scroll_bottom()
        return False

    def scroll_layout_changed(self, adjustment):
        self.update_status_size()
        self._scroll_layout = True
        self.queue_scroll_bottom()

    def scroll_position_changed(self, adjustment):
        if not self._scroll_setting and not self._scroll_layout:
            self._navigation_index = None
            bottom = max(0, adjustment.get_upper() - adjustment.get_page_size())
            self._follow_bottom = bottom - adjustment.get_value() <= 2
        self.update_navigation()

    def scroll_bottom(self):
        self._scroll_idle = None
        adjustment = self.scroll.get_vadjustment()
        self._scroll_setting = True
        try:
            if self._follow_bottom:
                adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        finally:
            self._scroll_setting = False
            self._scroll_layout = False
        self.update_navigation()
        return False

    def event(self, kind, data):
        if self.closed:
            return
        if kind == "submitted":
            pending = self.pending_submission
            if pending and pending[2] == data["text"]:
                draft, attachments, _ = pending
                self.pending_submission = None
                self.prompt_history.append(draft)
                self._prompt_history_index = None
                buffer = self.input.get_buffer()
                if buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == draft:
                    buffer.set_text("")
                for path, row in attachments.items():
                    if self.attachments.get(path) is row:
                        self.remove_attachment(path)
        elif kind == "models_state":
            self.models_loading = data["loading"]
            if self.models_loading:
                self.model_status.set_text("モデル一覧を取得中…")
            self.update_model_controls()
        elif kind == "models":
            self.login_box.set_visible(False)
            self.setup_box.set_visible(False)
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
            if data.get("setup_required"):
                self.setup_box.set_visible(True)
                self.login_box.set_visible(False)
                self.model_status.set_text("動作環境の準備が必要です。")
                self.status.set_text("下の「動作環境を準備する」を押してください。")
                self.setup_event("state", {"active": self.app.runtime.active})
                if self.app.runtime.active:
                    message, fraction = self.app.runtime.progress
                    self.setup_event("progress", {"message": message, "fraction": fraction})
            elif data.get("login_required"):
                self.login_box.set_visible(True)
                self.model_status.set_text(data["message"])
                self.status.set_text(data["message"])
            else:
                self.model_status.set_text("モデル一覧を取得できませんでした: " + data["message"] + " 新しいタブでお試しください。")
                self.status.set_text(self.model_status.get_text())
        elif kind == "thread":
            self.update_model_controls()
        elif kind == "user":
            if self.topic == "新しいお願い":
                self.topic = " ".join(data["text"].split())[:24]
                if self.page:
                    self.page.set_title(self.topic)
                    self.page.set_tooltip(data["text"][:200])
            self.add_message("あなた", data["text"])
            self.update_model_controls()
        elif kind == "summary":
            self.get_clipboard().set(data["text"])
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
            self.pending_submission = None
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
            self.prompt_history.clear()
            self._prompt_history_index = None
            self.clear_navigation_highlight()
            self.message_widgets.clear()
            self._navigation_index = None
            self.pending_submission = None
            self.clear_attachments()
            self.focus_input()
            while (child := self.history.get_first_child()) is not None:
                self.history.remove(child)
            self.messages.clear()
            self.update_navigation()
            for request_id in list(self.requests):
                self.remove_request(request_id)
            self.status.set_text(random.choice(STARTUP_TIPS))
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
            message = child.get_child() if child.has_css_class("errand-message-row") else child
            heading = message.get_first_child()
            content = heading.get_next_sibling()
            text = content.get_source() if isinstance(content, MarkdownView) else content.get_text()
            texts.append(heading.get_text() + "\n" + text)
            child = child.get_next_sibling()
        return "\n\n".join(texts)

    def copy(self, _):
        self.conversation_popover.popdown()
        self.get_clipboard().set(self.conversation_text())
        self.status.set_text("会話全文をコピーしました。")

    def copy_summary(self, _):
        self.conversation_popover.popdown()
        try:
            entry = self.selected_model()
            if entry is None:
                raise ValueError("モデルを選んでください。")
            self.session.summarize(self.conversation_text(), model=entry["model"], effort=self.selected_effort())
        except Exception as error:
            self.status.set_text(str(error))


class Window(Adw.ApplicationWindow):
    def __init__(self, app, codex):
        super().__init__(application=app, title="Errand — 小さなお願い",
                         default_width=640, default_height=740)
        self.app, self.codex = app, codex
        self.add_css_class("errand-window")
        self.conversations = []
        self.close_dialog = None
        self.connect("close-request", self.hide_on_close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)
        header = Adw.HeaderBar()
        root.append(header)
        self.new = Gtk.Button(label="＋", tooltip_text="新しいタブ（⌘+T）" if sys.platform == "darwin" else "新しいタブ（Ctrl+T）")
        self.new.update_property([Gtk.AccessibleProperty.LABEL], ["新しいタブ"])
        self.new.connect("clicked", lambda _: self.new_tab())
        header.pack_start(self.new)
        self.preferences_button = Gtk.Button(tooltip_text="環境設定")
        self.preferences_button.update_property([Gtk.AccessibleProperty.LABEL], ["環境設定"])
        menu_icon = Gtk.DrawingArea(width_request=16, height_request=16)
        def draw_menu(widget, context, width, height):
            color = widget.get_color()
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            context.set_line_width(1.5)
            for fraction in (.25, .5, .75):
                context.move_to(2, height * fraction)
                context.line_to(width - 2, height * fraction)
            context.stroke()
        menu_icon.set_draw_func(draw_menu)
        self.preferences_button.set_child(menu_icon)
        self.preferences_button.connect("clicked", lambda _: app.open_preferences())
        header.pack_end(self.preferences_button)
        self.tabs = Adw.TabView(vexpand=True, margin_top=10)
        # Keep document-boundary keys available to text widgets, including
        # Ctrl+Up/Down translated to Ctrl+Home/End by xremap.
        # All tab keys go through the configurable window shortcut dispatcher.
        self.tabs.set_shortcuts(Adw.TabViewShortcuts.NONE)
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
        self.refresh_shortcut_labels()
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
        if self.current and self.current.bookmark_button.menu.get_visible() and keyval == Gdk.KEY_Escape:
            self.current.bookmark_button.menu.popdown()
            return True
        if self.current and self.current.conversation_popover.get_visible() and keyval == Gdk.KEY_Escape:
            self.current.conversation_popover.popdown()
            return True
        if self.current and self.current.model_popover.get_visible():
            if keyval == Gdk.KEY_Escape:
                self.current.model_popover.popdown()
                return True
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if self.close_dialog is None:
            current_key = key_signature(keyval, modifiers)
            for action in ACTIONS:
                if any(current_key == signature(value) for value in self.app.action_bindings(action)):
                    self.run_shortcut(action)
                    return True
        if (not modifiers and self.close_dialog is None and self.current
                and self.current.input.has_focus() and keyval in (Gdk.KEY_Up, Gdk.KEY_Down)):
            if self.current.recall_prompt(-1 if keyval == Gdk.KEY_Up else 1):
                return True
        if (modifiers == Gdk.ModifierType.CONTROL_MASK and self.close_dialog is None
                and self.current and self.current.input.has_focus()
                and keyval in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End)):
            view = self.current.input
            buffer = view.get_buffer()
            buffer.place_cursor(buffer.get_start_iter() if keyval in (Gdk.KEY_Up, Gdk.KEY_Home)
                                else buffer.get_end_iter())
            view.scroll_mark_onscreen(buffer.get_insert())
            return True
        return False

    def run_shortcut(self, action):
        if action in ('previous-tab', 'next-tab'):
            if action == 'previous-tab':
                self.tabs.select_previous_page()
            else:
                self.tabs.select_next_page()
        elif action == 'new-tab':
            self.new_tab()
        elif action == 'close-tab':
            page = self.tabs.get_selected_page()
            if page:
                self.tabs.close_page(page)
        elif action == 'send' and self.current:
            self.current.send(None)
        elif action.endswith('-message') and self.current:
            self.current.navigate_messages({'previous-message': 'previous', 'next-message': 'next',
                                            'first-message': 'first', 'latest-message': 'latest'}[action])
        elif action in ('zoom-in', 'zoom-out', 'zoom-reset'):
            self.app.change_font_size({'zoom-in': 1, 'zoom-out': -1, 'zoom-reset': 0}[action])
        elif action == 'preferences':
            self.app.open_preferences()
        elif action == 'quit':
            self.quit_application()
        elif action == 'hide':
            self.set_visible(False)

    def refresh_shortcut_labels(self):
        self.new.set_tooltip_text(self.app.shortcut_title('new-tab'))
        for conversation in self.conversations:
            for action, button in conversation.navigation_buttons.items():
                name = {'first': 'first-message', 'previous': 'previous-message',
                        'next': 'next-message', 'latest': 'latest-message'}[action]
                button.set_tooltip_text(self.app.shortcut_title(name))


    def outside_click(self, gesture, count, x, y):
        if not self.current:
            return
        for popover, button in ((self.current.model_popover, self.current.model_button),
                                (self.current.bookmark_button.menu, self.current.bookmark_button),
                                (self.current.conversation_popover, self.current.conversation_menu)):
            if not popover.get_visible():
                continue
            target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
            input_clicked = False
            while target is not None:
                if target is self.current.input:
                    input_clicked = True
                if target in (popover, button):
                    break
                target = target.get_parent()
            if target is None:
                popover.popdown()
                if input_clicked:
                    # Popdown may restore focus to the menu button after
                    # the editor's own click handler has already run.
                    GLib.idle_add(self.current.focus_input)

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
    def __init__(self, codex=None, smoke=False, isolated=False, command=None, runtime=None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if smoke or isolated else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id="jp.jidaikobo.Errand", flags=flags)
        GLib.set_application_name("Errand")
        self.settings = load_settings(isolated=isolated or smoke)
        self.bookmarks = Bookmarks(self.settings.path.parent / 'bookmarks.json', isolated=isolated or smoke)
        codex = codex or self.settings.get("codex-path") or None
        self.codex, self.smoke = codex, smoke
        self.command = command
        self.runtime = runtime or RuntimeSetup(dispatch=GLib.idle_add)
        self.window = None
        self.preferences = None
        self.font_css = Gtk.CssProvider()
        self.mac_shortcut = None
        self.registered_shortcut = ""
        self.shortcut_error = None
        self.connect("activate", self.activate_window)
        self.connect("shutdown", self.shutdown_session)
        if sys.platform == "darwin":
            preferences_action = Gio.SimpleAction.new("preferences", None)
            preferences_action.connect("activate", lambda *_: self.open_preferences())
            self.add_action(preferences_action)
            self.set_accels_for_action('app.preferences', list(self.action_bindings('preferences')))
            action = Gio.SimpleAction.new("quit", None)
            action.connect("activate", lambda *_: self.window.quit_application() if self.window else self.quit())
            self.add_action(action)
            self.set_accels_for_action('app.quit', list(self.action_bindings('quit')))
            menu = Gio.Menu()
            application_menu = Gio.Menu()
            application_menu.append("環境設定…", "app.preferences")
            application_menu.append("Errandを終了", "app.quit")
            menu.append_submenu("Errand", application_menu)
            self.connect("startup", lambda _: self.set_menubar(menu))

    def apply_font(self):
        size = self.settings.get("font-size")
        self.font_css.load_from_data(f".errand-window {{ font-size: {size}pt; }}".encode())
        if self.window:
            from .math_widget import MathBlock
            def resize(widget):
                if isinstance(widget, MathBlock):
                    widget.set_font_size(size)
                child = widget.get_first_child()
                while child:
                    resize(child)
                    child = child.get_next_sibling()
            resize(self.window)

    def register_shortcut(self, value):
        if sys.platform != "darwin" or self.settings.isolated or value == self.registered_shortcut:
            return
        key, modifiers = mac_key(value)
        if self.mac_shortcut is None:
            from .macos_shortcut import MacShortcut
            self.mac_shortcut = MacShortcut(lambda: GLib.idle_add(self.activate))
        self.mac_shortcut.replace(key, modifiers)
        self.registered_shortcut = value

    def change_font_size(self, step):
        size = (max(8, min(28, self.settings.get("font-size") + step))
                if step else DEFAULTS["font-size"])
        try:
            self.settings.set_font_size(size)
        except (OSError, ValueError) as exc:
            if self.window and self.window.current:
                self.window.current.status.set_text(str(exc))
            return
        self.apply_font()
        if self.preferences:
            self.preferences.font.set_value(size)

    def action_bindings(self, action):
        return bindings(action, self.settings.get('shortcuts'))

    def shortcut_title(self, action):
        from .preferences import accelerator_label
        keys = ' / '.join(accelerator_label(value) for value in self.action_bindings(action)) or '無効'
        return f'{ACTIONS[action][0]}（{keys}）'

    def apply_shortcuts(self):
        if sys.platform == 'darwin':
            self.set_accels_for_action('app.preferences', list(self.action_bindings('preferences')))
            self.set_accels_for_action('app.quit', list(self.action_bindings('quit')))
        if self.window:
            self.window.refresh_shortcut_labels()

    def save_preferences(self, values):
        values = validate(values)
        validate_shortcuts(values['shortcuts'], values['open-errand'])
        old_shortcut = self.registered_shortcut
        self.register_shortcut(values["open-errand"])
        try:
            self.settings.save(values)
        except Exception:
            self.register_shortcut(old_shortcut)
            raise
        self.codex = values["codex-path"] or None
        if self.window:
            self.window.codex = self.codex
        self.apply_font()
        self.apply_shortcuts()

    def open_preferences(self):
        if self.window is None:
            self.activate()
        if self.preferences is None:
            self.preferences = Preferences(self)
            self.preferences.connect("close-request", self.preferences_closed)
        self.preferences.present()

    def preferences_closed(self, *_):
        self.preferences = None
        return False

    def activate_window(self, _):
        if self.window is None:
            self.apply_font()
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.font_css,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            self.window = Window(self, self.codex)
            self.apply_shortcuts()
            try:
                self.register_shortcut(self.settings.get("open-errand"))
            except (ValueError, OSError, RuntimeError) as exc:
                self.shortcut_error = str(exc)
                self.window.current.status.set_text(self.shortcut_error)
            self.hold()
        self.window.present()
        if self.smoke:
            GLib.timeout_add(250, lambda: (self.quit(), False)[1])

    def shutdown_session(self, _):
        self.runtime.close()
        if self.mac_shortcut:
            self.mac_shortcut.close()
        if self.preferences:
            self.preferences.destroy()
        if Gdk.Display.get_default():
            Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.font_css)
        if self.window:
            self.window.shutdown_sessions()


def main():
    parser = argparse.ArgumentParser(description="Errand — 小さなCodexチャット")
    parser.add_argument("--codex", help="Codex実行ファイルの絶対パス")
    parser.add_argument("--smoke-test", action="store_true", help="表示後に終了（Codexへは接続しない）")
    parser.add_argument("--runtime-check-root", type=Path,
                        help="開発用：指定した新規フォルダーへCodexを取得して動作検証")
    parser.add_argument("--runtime-check-arch", choices=("arm64", "x86_64"),
                        help="開発用：取得・検証するCodexのCPU")
    args = parser.parse_args()
    if args.runtime_check_root:
        import threading
        import platform
        from .runtime import install_runtime
        root = args.runtime_check_root.resolve()
        if root.exists():
            parser.error("検証用の出力先は新規フォルダーを指定してください。")
        def report(message, fraction):
            if fraction is None:
                print(message, flush=True)
        install_runtime(root, args.runtime_check_arch or platform.machine(), threading.Event(), report)
        print("PASS: official download, checksum and app-server handshake", flush=True)
        return 0
    if not Gtk.init_check():
        parser.exit(1, "画面へ接続できません。デスクトップのTerminalから起動してください。\n")
    return Application(args.codex, args.smoke_test).run([sys.argv[0]])
