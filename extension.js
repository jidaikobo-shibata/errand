import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

export default class ErrandExtension extends Extension {
    enable() {
        this._button = new PanelMenu.Button(0.0, 'Errand — 小さなお願い', true);
        this._button.accessible_name = 'Errand — 小さなお願いを開く';
        this._button.add_child(new St.Icon({
            icon_name: 'system-run-symbolic',
            style_class: 'system-status-icon',
        }));
        this._button.connect('button-press-event', () => {
            this._launch();
            return true;
        });
        this._button.connect('key-press-event', (_actor, event) => {
            const key = event.get_key_symbol();
            // Return / space, including keypad Enter.
            if ([65293, 32, 65421].includes(key)) {
                this._launch();
                return true;
            }
            return false;
        });
        Main.panel.addToStatusArea(this.uuid, this._button);
        Main.wm.addKeybinding('open-errand', this.getSettings(),
            Meta.KeyBindingFlags.NONE, Shell.ActionMode.NORMAL,
            () => this._launch());
    }

    _launch() {
        try {
            const window = global.get_window_actors()
                .map(actor => actor.meta_window)
                .find(candidate => candidate.get_gtk_application_id() === 'jp.jidaikobo.Errand');
            if (window) {
                Main.activateWindow(window, global.get_current_time());
                return;
            }
            const argv = ['/usr/bin/python3', `${this.path}/app.py`];
            const codex = this.getSettings().get_string('codex-path');
            if (codex)
                argv.push('--codex', codex);
            const context = global.create_app_launch_context(global.get_current_time(), -1);
            // A desktop session may not inherit nvm's PATH.
            context.setenv('PATH', '/usr/local/bin:/usr/bin:/bin');
            const app = Gio.AppInfo.create_from_commandline(
                argv.map(arg => GLib.shell_quote(arg)).join(' '),
                'Errand', Gio.AppInfoCreateFlags.NONE);
            app.launch([], context);
        } catch (error) {
            console.error(`Errand: ${error.message}`);
            Main.notify('Errandを開けませんでした', error.message);
        }
    }

    disable() {
        Main.wm.removeKeybinding('open-errand');
        this._button?.destroy();
        this._button = null;
    }
}
