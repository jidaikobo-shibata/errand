import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gtk from 'gi://Gtk';

export function shortcutName(settings) {
    const names = settings.get_strv('open-errand').map(accelerator => {
        const [ok, key, modifiers] = Gtk.accelerator_parse(accelerator);
        return ok ? Gtk.accelerator_get_label(key, modifiers) : accelerator;
    });
    return names.join(' / ') || '無効';
}

export function captureShortcut(parent, settings) {
    const dialog = new Gtk.Window({
        title: '起動キーを設定', modal: true, transient_for: parent,
        default_width: 440, default_height: 180,
    });
    const box = new Gtk.Box({
        orientation: Gtk.Orientation.VERTICAL, spacing: 16,
        margin_top: 20, margin_bottom: 20, margin_start: 20, margin_end: 20,
    });
    const prompt = new Gtk.Label({
        label: '起動に使うキーの組み合わせを押してください。\nEscでキャンセル、Backspaceで無効化できます。',
        wrap: true, xalign: 0,
    });
    box.append(prompt);
    const cancel = new Gtk.Button({label: 'キャンセル'});
    cancel.connect('clicked', () => dialog.close());
    box.append(cancel);
    dialog.set_child(box);
    const keys = new Gtk.EventControllerKey();
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE);
    keys.connect('key-pressed', (_controller, key, _code, state) => {
        const modifiers = state & Gtk.accelerator_get_default_mod_mask();
        if (key === Gdk.KEY_Escape && modifiers === 0) {
            dialog.close();
            return true;
        }
        if (key === Gdk.KEY_BackSpace && modifiers === 0) {
            settings.set_strv('open-errand', []);
            dialog.close();
            return true;
        }
        const lower = Gdk.keyval_to_lower(key);
        const commandModifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
            Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.HYPER_MASK;
        const functionKey = lower >= Gdk.KEY_F1 && lower <= Gdk.KEY_F35;
        if (!Gtk.accelerator_valid(lower, modifiers))
            return true;
        if (!(modifiers & commandModifiers) && !functionKey) {
            prompt.set_label('Ctrl・Alt・Superなどと組み合わせるか、ファンクションキーを使ってください。\nEscでキャンセルできます。');
            return true;
        }
        settings.set_strv('open-errand', [Gtk.accelerator_name(lower, modifiers)]);
        dialog.close();
        return true;
    });
    dialog.add_controller(keys);
    dialog.connect('map', () => dialog.get_surface().inhibit_system_shortcuts(null));
    dialog.connect('unmap', () => dialog.get_surface()?.restore_system_shortcuts());
    // Exposed to the local smoke test; it sends signals without changing real settings.
    dialog.shortcutController = keys;
    dialog.present();
    return dialog;
}

export function shortcutGroup(window, settings) {
    const group = new Adw.PreferencesGroup({
        title: '起動ショートカット',
        description: '変更ボタンを押して、使いたいキーの組み合わせを入力してください。',
    });
    const row = new Adw.ActionRow({title: 'Errandを開く'});
    const change = new Gtk.Button({label: '変更', valign: Gtk.Align.CENTER});
    change.connect('clicked', () => captureShortcut(window, settings));
    row.add_suffix(change);
    row.set_activatable_widget(change);
    const disable = new Gtk.Button({label: '無効化', valign: Gtk.Align.CENTER});
    disable.connect('clicked', () => settings.set_strv('open-errand', []));
    row.add_suffix(disable);
    const reset = new Gtk.Button({label: '既定値に戻す', valign: Gtk.Align.CENTER});
    reset.connect('clicked', () => settings.reset('open-errand'));
    row.add_suffix(reset);
    const refresh = () => row.set_subtitle(shortcutName(settings));
    refresh();
    const connection = settings.connect('changed::open-errand', refresh);
    window.connect('close-request', () => {
        settings.disconnect(connection);
        return false;
    });
    group.add(row);
    return group;
}
