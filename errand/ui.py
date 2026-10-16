"""Small, keyboard-accessible GTK window for disposable conversations."""

import argparse
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .services import CodexServices
from .preferences import Preferences, load_settings, mac_key
from .settings import DEFAULTS, validate
from .shortcuts import ACTIONS, bindings, key_signature, signature, validate_shortcuts
from .bookmarks import Bookmarks
from .bookmark_widget import BookmarkMenu


# Retained as an import-compatible name for existing callers.
from .product import ERRAND, STARTUP_TIPS


# Preserve existing import paths for callers and smoke checks.
from chat_foundation.gtk.conversation import Conversation, label, status_label, effort_title


class Window(Adw.ApplicationWindow):
    def __init__(self, app, codex):
        super().__init__(application=app, title=app.product.window_title,
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
        dialog = Adw.AlertDialog(heading=f"作業を停止して{self.get_application().product.name}を終了しますか？",
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
    def __init__(self, codex=None, smoke=False, isolated=False, command=None, runtime=None, product=ERRAND,
                 services=None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if smoke or isolated else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=product.application_id, flags=flags)
        self.product = product
        self.services = services if services is not None else CodexServices(product)
        GLib.set_application_name(product.name)
        self.settings = load_settings(isolated=isolated or smoke, product=product)
        self.bookmarks = Bookmarks(self.settings.path.parent / 'bookmarks.json', isolated=isolated or smoke)
        codex = codex or self.settings.get("codex-path") or None
        self.codex, self.smoke = codex, smoke
        self.command = command
        self.runtime = runtime or self.services.runtime(dispatch=GLib.idle_add)
        self.window = None
        self.preferences = None
        self.font_css = Gtk.CssProvider()
        self.mac_shortcut = None
        self.mac_reopen = None
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
            application_menu.append(f"{product.name}を終了", "app.quit")
            menu.append_submenu(product.name, application_menu)
            self.connect("startup", lambda _: self.set_menubar(menu))

    def create_bookmark_menu(self, conversation):
        return BookmarkMenu(conversation)

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
        values = validate({**{key: self.settings.get(key) for key in DEFAULTS}, **values})
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
            if sys.platform == 'darwin':
                from .macos_reopen import MacReopen
                try:
                    self.mac_reopen = MacReopen(lambda: GLib.idle_add(self.activate))
                except (OSError, RuntimeError) as exc:
                    self.window.current.status.set_text(str(exc))
            self.hold()
        self.window.present()
        if self.smoke:
            GLib.timeout_add(250, lambda: (self.quit(), False)[1])

    def shutdown_session(self, _):
        if self.mac_reopen:
            self.mac_reopen.close()
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
