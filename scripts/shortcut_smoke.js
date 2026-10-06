import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';
import {captureShortcut, shortcutGroup, shortcutName} from '../shortcutPreferences.js';

const root = GLib.path_get_dirname(GLib.path_get_dirname(Gio.File.new_for_uri(import.meta.url).get_path()));
const source = Gio.SettingsSchemaSource.new_from_directory(`${root}/schemas`, Gio.SettingsSchemaSource.get_default(), false);
const settings = new Gio.Settings({settings_schema: source.lookup('org.gnome.shell.extensions.errand', false)});
function check(value, message) {
    if (!value)
        throw new Error(message);
}
const app = new Adw.Application({application_id: 'jp.jidaikobo.Errand.ShortcutTest', flags: Gio.ApplicationFlags.NON_UNIQUE});
let failure = null;
app.connect('activate', () => {
    const window = new Adw.PreferencesWindow({application: app});
    const page = new Adw.PreferencesPage();
    page.add(shortcutGroup(window, settings));
    window.add(page);
    window.present();
    GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
        try {
            let dialog = captureShortcut(window, settings);
            const modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK;
            dialog.shortcutController.emit('key-pressed', Gdk.KEY_F8, 0, modifiers);
            const [ok, key, savedModifiers] = Gtk.accelerator_parse(settings.get_strv('open-errand')[0]);
            check(ok && key === Gdk.KEY_F8 && savedModifiers === modifiers, 'Shortcut capture failed');
            check(shortcutName(settings).includes('F8'), 'Current shortcut label failed');
            const saved = settings.get_strv('open-errand')[0];
            dialog = captureShortcut(window, settings);
            dialog.shortcutController.emit('key-pressed', Gdk.KEY_e, 0, 0);
            check(settings.get_strv('open-errand')[0] === saved, 'Unmodified letters must not be saved');
            dialog.shortcutController.emit('key-pressed', Gdk.KEY_Escape, 0, 0);
            check(settings.get_strv('open-errand')[0] === saved, 'Cancel changed the setting');
            dialog = captureShortcut(window, settings);
            dialog.shortcutController.emit('key-pressed', Gdk.KEY_BackSpace, 0, 0);
            check(settings.get_strv('open-errand').length === 0 && shortcutName(settings) === '無効', 'Disable failed');
            settings.reset('open-errand');
            check(settings.get_strv('open-errand')[0] === '<Super><Shift>e', 'Reset failed');
            print('PASS: shortcut preference UI, capture, validation, cancel, disable and reset');
        } catch (error) {
            failure = error;
        }
        window.close();
        app.quit();
        return GLib.SOURCE_REMOVE;
    });
});
app.run([]);
if (failure)
    throw failure;
