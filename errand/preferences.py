"""Application preferences shared by desktop launchers."""
from pathlib import Path
import sys
import threading
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .settings import Settings
from .product import ERRAND
from .shortcuts import ACTIONS, bindings, validate_shortcuts


def load_settings(isolated=False, product=ERRAND):
    settings = Settings(path=product.settings_path(), isolated=isolated)
    if sys.platform != 'darwin' and not isolated and product.settings_schema:
        source = Gio.SettingsSchemaSource.get_default()
        directory = Path(__file__).resolve().parent.parent / 'schemas'
        if (directory / 'gschemas.compiled').is_file():
            source = Gio.SettingsSchemaSource.new_from_directory(str(directory), source, False)
        schema = source.lookup(product.settings_schema, True) if source else None
        if schema:
            settings.gnome = Gio.Settings.new_full(schema, None, None)
    try:
        validate_shortcuts(settings.get('shortcuts'), settings.get('open-errand'))
    except ValueError as exc:
        settings.error = f'ショートカットの設定を読み込めませんでした: {exc}'
        settings.values['shortcuts'] = {}
    return settings


def mac_key(accelerator):
    from .macos_shortcut import KEYS
    if not accelerator:
        return None, 0
    valid, key, modifiers = Gtk.accelerator_parse(accelerator)
    name = Gdk.keyval_name(Gdk.keyval_to_lower(key))
    if not valid or name not in KEYS:
        raise ValueError('このキーはmacOSの起動キーに対応していません。英字・数字・Fキーを指定してください。')
    flags = ((256 if modifiers & (Gdk.ModifierType.META_MASK | Gdk.ModifierType.SUPER_MASK) else 0)
             | (512 if modifiers & Gdk.ModifierType.SHIFT_MASK else 0)
             | (2048 if modifiers & Gdk.ModifierType.ALT_MASK else 0)
             | (4096 if modifiers & Gdk.ModifierType.CONTROL_MASK else 0))
    return KEYS[name], flags


def accelerator_label(value):
    if not value:
        return '無効'
    valid, key, modifiers = Gtk.accelerator_parse(value)
    return Gtk.accelerator_get_label(key, modifiers) if valid else value


class Preferences(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(title='環境設定', transient_for=app.window, modal=True,
                         default_width=540, default_height=450)
        self.app = app
        self.add_css_class('errand-window')
        page = Adw.PreferencesPage(title='一般', name='general')
        self.add(page)
        display = Adw.PreferencesGroup(title='表示')
        self.font = Adw.SpinRow.new_with_range(8, 28, 1)
        self.font.set_title('文字サイズ（pt）')
        self.font.set_value(app.settings.get('font-size'))
        display.add(self.font)
        page.add(display)
        ai = Adw.PreferencesGroup(title='AI', description='変更は新しいタブから適用します。既存の会話は引き継ぎません。')
        self.provider_ids = ('codex', 'gemini-cli')
        self.provider = Adw.ComboRow(title='新しいタブで使うAI', model=Gtk.StringList.new([
            'Codex', 'Gemini']))
        self.provider.set_selected(self.provider_ids.index(app.settings.get('ai-provider')))
        ai.add(self.provider)
        page.add(ai)
        gemini = Adw.PreferencesGroup(title='Gemini')
        from .secret_store import SecretStore
        self.credential_store = getattr(app.services, 'credential_store', None) or SecretStore(
            app.product.application_id, isolated=app.settings.isolated)
        self._key_closed = self._key_busy = False
        self._key_saved = False
        self.key_entry = Adw.PasswordEntryRow(title='APIキー')
        self.key_entry.set_tooltip_text('Google AI Studioで発行したキーを貼り付けて、キーを保存を押してください。')
        gemini.add(self.key_entry)
        self.key_status = Adw.ActionRow(title='OSの保管庫', subtitle='保存状態を確認中…')
        self.key_save = Gtk.Button(label='キーを保存', valign=Gtk.Align.CENTER)
        self.key_delete = Gtk.Button(label='削除', valign=Gtk.Align.CENTER)
        self.key_delete.set_tooltip_text('ErrandがOSの保管庫に保存したキーだけを削除します。既存の環境変数やキー設定ファイルは変更しません。')
        self.key_save.add_css_class('suggested-action')
        self.key_save.connect('clicked', lambda _: self.key_action('save'))
        self.key_delete.connect('clicked', lambda _: self.key_action('delete'))
        self.key_entry.connect('changed', lambda _: self.update_key_controls())
        self.key_status.add_suffix(self.key_save)
        self.key_status.add_suffix(self.key_delete)
        gemini.add(self.key_status)
        self.connect('close-request', self.key_closed)
        self.key_action('status')
        advanced = Adw.ExpanderRow(title='詳細設定', subtitle='既存のキー設定ファイル・CLI・MCP')
        gemini.add(advanced)
        self.gemini_env = self.add_path_entry(
            advanced, 'APIキー設定ファイルの絶対パス', app.settings.get('gemini-env-path'),
            '空欄なら既定のキー設定ファイルを使用')
        self.gemini_env.set_tooltip_text('OSの保管庫にキーがない場合、GEMINI_API_KEY環境変数、指定ファイルの順で読み込みます。')
        self.gemini_path = self.add_path_entry(
            advanced, 'Gemini CLI実行ファイルの絶対パス', app.settings.get('gemini-cli-path'),
            '空欄なら自動で検索します')
        self.gemini_mcp = self.add_path_entry(
            advanced, 'MCP設定ファイルの絶対パス', app.settings.get('gemini-mcp-config'),
            '任意：MCPを使う場合に指定')
        self.gemini_mcp.set_tooltip_text('指定した設定のMCP接続を新しいタブで利用します。認証や承認設定は取り込みません。')
        gemini.add(Adw.ActionRow(title='APIの利用条件', subtitle=(
            'キーはOSの保管庫に保存します。設定ファイルには保存しません。'
            '無料枠・料金・データ利用条件はGoogle AI Studioで確認してください。')))
        page.add(gemini)
        codex = Adw.PreferencesGroup(title='Codex')
        self.path = self.add_path_entry(
            codex, '実行ファイルの絶対パス', app.settings.get('codex-path'),
            '空欄なら自動で検索します')
        page.add(codex)
        self.shortcut_value = app.settings.get('open-errand')
        self.shortcut_overrides = app.settings.get('shortcuts')
        self.shortcut_rows = {}
        keyboard = Adw.PreferencesPage(title='キーボード・ショートカット', name='keyboard')
        self.add(keyboard)
        group = Adw.PreferencesGroup(title='起動', description=(
            'macOSではErrandが起動している間に使えます。' if sys.platform == 'darwin' else
            'GNOME拡張と同じ起動キーです。拡張を有効にして使ってください。'))
        self.add_shortcut_row(group, 'open-errand', 'Errandを開く')
        self.shortcut = self.shortcut_rows['open-errand']
        self.shortcut.set_sensitive(sys.platform == 'darwin' or app.settings.gnome is not None)
        keyboard.add(group)
        local = Adw.PreferencesGroup(title='アプリ内の操作', description=
            'OS側のショートカットと競合するキーはErrandに届きません。変更は保存後に反映します。')
        for action, (title, _) in ACTIONS.items():
            self.add_shortcut_row(local, action, title)
        keyboard.add(local)
        controls = Adw.PreferencesGroup()
        self.message = Gtk.Label(wrap=True, xalign=0)
        self.message.set_wrap_mode(2)
        self.message.set_text(app.settings.error or '')
        controls.add(self.message)
        self.save_button = Gtk.Button(label='保存', halign=Gtk.Align.END)
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self.save)
        controls.add(self.save_button)
        page.add(controls)
        keyboard_controls = Adw.PreferencesGroup()
        self.keyboard_message = Gtk.Label(wrap=True, xalign=0)
        keyboard_controls.add(self.keyboard_message)
        keyboard_save = Gtk.Button(label='保存', halign=Gtk.Align.END)
        keyboard_save.add_css_class('suggested-action')
        keyboard_save.connect('clicked', self.save)
        keyboard_controls.add(keyboard_save)
        keyboard.add(keyboard_controls)
        self.connect('map', self.hide_page_icons)

    def hide_page_icons(self, *_):
        # ViewSwitcher expects page icons; these pages use text-only tabs.
        def visit(widget, in_switcher=False):
            in_switcher = in_switcher or isinstance(widget, Adw.ViewSwitcher)
            if in_switcher and isinstance(widget, Gtk.Image):
                widget.set_visible(False)
            child = widget.get_first_child()
            while child is not None:
                visit(child, in_switcher)
                child = child.get_next_sibling()
        visit(self)

    def row_label(self, action):
        values = (self.shortcut_value,) if action == 'open-errand' else bindings(action, self.shortcut_overrides)
        return ' / '.join(accelerator_label(value) for value in values) if values else '無効'

    def add_shortcut_row(self, group, action, title):
        row = Adw.ActionRow(title=title, subtitle=self.row_label(action))
        self.shortcut_rows[action] = row
        change = Gtk.Button(label='変更', valign=Gtk.Align.CENTER)
        change.connect('clicked', lambda _: self.capture(action=action))
        row.add_suffix(change)
        row.set_activatable_widget(change)
        for text, callback in (('無効化', lambda _: self.set_shortcut('', action)),
                               ('既定値', lambda _: self.reset_shortcut(action))):
            button = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
            button.connect('clicked', callback)
            row.add_suffix(button)
        group.add(row)

    def set_shortcut(self, value, action='open-errand'):
        if action == 'open-errand':
            self.shortcut_value = value
        else:
            self.shortcut_overrides[action] = value
        self.shortcut_rows[action].set_subtitle(self.row_label(action))

    def reset_shortcut(self, action):
        if action == 'open-errand':
            default = self.app.settings.gnome.get_default_value('open-errand').unpack() if self.app.settings.gnome else []
            self.set_shortcut(default[0] if default else '')
        else:
            self.shortcut_overrides.pop(action, None)
            self.shortcut_rows[action].set_subtitle(self.row_label(action))

    def key_closed(self, *_):
        self._key_closed = True
        self.key_entry.set_text('')
        return False

    def update_key_controls(self):
        self.key_save.set_sensitive(not self._key_busy and bool(self.key_entry.get_text().strip()))
        self.key_delete.set_sensitive(not self._key_busy and self._key_saved)
        self.key_entry.set_sensitive(not self._key_busy)

    def key_action(self, action):
        if self._key_busy or self._key_closed:
            return
        key = self.key_entry.get_text() if action == 'save' else None
        self._key_busy = True
        self.key_status.set_subtitle('OSの保管庫にアクセスしています…')
        self.update_key_controls()

        def work():
            try:
                if action == 'save':
                    self.credential_store.save(key)
                    saved = True
                elif action == 'delete':
                    self.credential_store.delete()
                    saved = False
                else:
                    saved = self.credential_store.load() is not None
                error = None
            except Exception:
                saved = self._key_saved
                error = '保管庫を利用できませんでした。ロック状態やOSのアクセス許可を確認してください。平文では保存しません。'
            GLib.idle_add(finish, saved, error)

        def finish(saved, error):
            if self._key_closed:
                return False
            self._key_busy = False
            self._key_saved = saved
            if not error:
                self.key_entry.set_text('')
            self.key_status.set_subtitle(error or (
                '保存済み。変更する場合は新しいキーを入力してください。' if saved else
                '未保存。APIキーを入力して「キーを保存」を押してください。'))
            self.update_key_controls()
            return False

        threading.Thread(target=work, daemon=True, name='credential-store').start()

    @staticmethod
    def add_path_entry(group, title, value, placeholder):
        # EntryRow uses its title as a floating placeholder and has no separate
        # placeholder property. Keep a persistent label and a real Gtk.Entry.
        row = Adw.PreferencesRow(title=title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        caption = Gtk.Label(label=title, xalign=0, wrap=True)
        caption.add_css_class('caption')
        caption.add_css_class('dim-label')
        entry = Gtk.Entry(text=value, placeholder_text=placeholder, has_frame=False,
                          hexpand=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [title])
        caption.set_mnemonic_widget(entry)
        box.append(caption)
        box.append(entry)
        row.set_child(box)
        if isinstance(group, Adw.ExpanderRow):
            group.add_row(row)
        else:
            group.add(row)
        return entry

    def save(self, *_):
        try:
            self.app.save_preferences({'font-size': int(self.font.get_value()),
                                       'ai-provider': self.provider_ids[self.provider.get_selected()],
                                       'gemini-env-path': self.gemini_env.get_text().strip(),
                                       'gemini-cli-path': self.gemini_path.get_text().strip(),
                                       'gemini-mcp-config': self.gemini_mcp.get_text().strip(),
                                       'codex-path': self.path.get_text().strip(),
                                       'open-errand': self.shortcut_value,
                                       'shortcuts': self.shortcut_overrides})
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.set_text(str(exc))
            self.keyboard_message.set_text(str(exc))
            return
        self.close()

    def capture(self, *_, action='open-errand'):
        dialog = Gtk.Window(title='ショートカットを設定', transient_for=self, modal=True,
                            default_width=440, default_height=180)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        prompt = Gtk.Label(label='割り当てるキーを押してください。\nEscでキャンセル、Backspaceで無効化できます。',
                           wrap=True, xalign=0)
        box.append(prompt)
        cancel = Gtk.Button(label='キャンセル')
        cancel.connect('clicked', lambda _: dialog.close())
        box.append(cancel)
        dialog.set_child(box)
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def pressed(_controller, key, code, state):
            modifiers = state & Gtk.accelerator_get_default_mod_mask()
            if key == Gdk.KEY_Escape and not modifiers:
                dialog.close()
                return True
            if key == Gdk.KEY_BackSpace and not modifiers:
                self.set_shortcut('', action)
                dialog.close()
                return True
            key = Gdk.keyval_to_lower(key)
            command = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                       Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
            if not Gtk.accelerator_valid(key, modifiers) or (not modifiers & command and not Gdk.KEY_F1 <= key <= Gdk.KEY_F35):
                prompt.set_text('Ctrl・Alt・Command・Superなどと組み合わせるか、Fキーを使ってください。')
                return True
            value = Gtk.accelerator_name(key, modifiers)
            if sys.platform == 'darwin' and action == 'open-errand':
                try:
                    mac_key(value)
                except ValueError as exc:
                    prompt.set_text(str(exc))
                    return True
            self.set_shortcut(value, action)
            dialog.close()
            return True
        controller.connect('key-pressed', pressed)
        dialog.add_controller(controller)
        def mapped(widget):
            surface = widget.get_surface()
            if surface:
                surface.inhibit_system_shortcuts(None)
        dialog.connect('map', mapped)
        dialog.connect('unmap', lambda widget: widget.get_surface().restore_system_shortcuts() if widget.get_surface() else None)
        self.capture_dialog = dialog
        self.capture_controller = controller
        dialog.present()
