"""Application preferences shared by desktop launchers."""
from pathlib import Path
import sys
from gi.repository import Adw, Gdk, Gio, Gtk

from .settings import Settings


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
        page = Adw.PreferencesPage()
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
        group = Adw.PreferencesGroup(title='起動ショートカット')
        self.shortcut = Adw.ActionRow(title='Errandを開く', subtitle=accelerator_label(self.shortcut_value))
        change = Gtk.Button(label='変更', valign=Gtk.Align.CENTER)
        change.connect('clicked', self.capture)
        self.shortcut.add_suffix(change)
        self.shortcut.set_activatable_widget(change)
        disable = Gtk.Button(label='無効化', valign=Gtk.Align.CENTER)
        disable.connect('clicked', lambda _: self.set_shortcut(''))
        self.shortcut.add_suffix(disable)
        available = sys.platform == 'darwin' or app.settings.gnome is not None
        self.shortcut.set_sensitive(available)
        group.set_description('macOSではErrandが起動している間に使えます。' if sys.platform == 'darwin' else
                              'GNOME拡張と同じ設定です。拡張を有効にして使ってください。' if available else
                              'この環境では起動キーの登録に対応していません。')
        group.add(self.shortcut)
        page.add(group)
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

    def set_shortcut(self, value):
        self.shortcut_value = value
        self.shortcut.set_subtitle(accelerator_label(value))

    def save(self, *_):
        try:
            self.app.save_preferences({'font-size': int(self.font.get_value()),
                                       'codex-path': self.path.get_text().strip(),
                                       'open-errand': self.shortcut_value})
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.set_text(str(exc))
            return
        self.close()

    def capture(self, *_):
        dialog = Gtk.Window(title='起動キーを設定', transient_for=self, modal=True,
                            default_width=440, default_height=180)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        prompt = Gtk.Label(label='起動に使うキーを押してください。\nEscでキャンセル、Backspaceで無効化できます。',
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
                self.set_shortcut('')
                dialog.close()
                return True
            key = Gdk.keyval_to_lower(key)
            command = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                       Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
            if not Gtk.accelerator_valid(key, modifiers) or (not modifiers & command and not Gdk.KEY_F1 <= key <= Gdk.KEY_F35):
                prompt.set_text('Ctrl・Alt・Command・Superなどと組み合わせるか、Fキーを使ってください。')
                return True
            value = Gtk.accelerator_name(key, modifiers)
            if sys.platform == 'darwin':
                try:
                    mac_key(value)
                except ValueError as exc:
                    prompt.set_text(str(exc))
                    return True
            self.set_shortcut(value)
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
