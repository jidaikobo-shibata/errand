import Adw from 'gi://Adw';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';
import {shortcutGroup} from './shortcutPreferences.js';

export default class ErrandPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({title: 'Errand'});
        page.add(shortcutGroup(window, settings));
        const display = new Adw.PreferencesGroup({title: '表示'});
        const font = Adw.SpinRow.new_with_range(8, 28, 1);
        font.title = '文字サイズ（pt）';
        settings.bind('font-size', font, 'value', 0);
        display.add(font);
        page.add(display);
        const group = new Adw.PreferencesGroup({
            title: 'Codex',
            description: '空欄ならPATHとnvmから検索します。',
        });
        const row = new Adw.EntryRow({title: 'Codex実行ファイルの絶対パス'});
        row.text = settings.get_string('codex-path');
        row.connect('notify::text', () => settings.set_string('codex-path', row.text));
        group.add(row);
        page.add(group);
        window.add(page);
    }
}
