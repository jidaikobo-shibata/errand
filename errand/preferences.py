"""Application preferences shared by desktop launchers."""
from pathlib import Path
import sys
from gi.repository import Adw, Gdk, Gio, Gtk

from .settings import Settings
from .shortcuts import ACTIONS, bindings, validate_shortcuts


def load_settings(isolated=False):
    settings = Settings(isolated=isolated)
    if sys.platform != 'darwin' and not isolated:
        source = Gio.SettingsSchemaSource.get_default()
        directory = Path(__file__).resolve().parent.parent / 'schemas'
        if (directory / 'gschemas.compiled').is_file():
            source = Gio.SettingsSchemaSource.new_from_directory(str(directory), source, False)
        schema = source.lookup('org.gnome.shell.extensions.errand', True) if source else None
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
        codex = Adw.PreferencesGroup(title='Codex', description='変更は新しいタブから適用します。空欄なら自動で検索します。')
        self.path = Adw.EntryRow(title='実行ファイルの絶対パス')
        self.path.set_text(app.codex or '')
        codex.add(self.path)
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

    def save(self, *_):
        try:
            self.app.save_preferences({'font-size': int(self.font.get_value()),
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
