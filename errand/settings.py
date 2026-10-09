"""Local UI preferences; never changes Codex configuration or credentials."""
import json
from copy import deepcopy
from .shortcuts import ACTIONS
import os
from pathlib import Path
import sys
import tempfile

DEFAULTS = {'font-size': 11, 'codex-path': '', 'open-errand': '', 'shortcuts': {}}


def validate(values):
    result = deepcopy(DEFAULTS)
    result.update({key: values[key] for key in DEFAULTS if key in values})
    size = result['font-size']
    if isinstance(size, bool) or not isinstance(size, int) or not 8 <= size <= 28:
        raise ValueError('文字サイズは8〜28ptで指定してください。')
    for key in ('codex-path', 'open-errand'):
        if not isinstance(result[key], str):
            raise ValueError('設定の形式が正しくありません。')
    shortcuts = result['shortcuts']
    if (not isinstance(shortcuts, dict) or set(shortcuts) - set(ACTIONS)
            or any(not isinstance(value, str) for value in shortcuts.values())):
        raise ValueError('キーボード・ショートカットの設定が正しくありません。')
    result['shortcuts'] = dict(shortcuts)
    path = result['codex-path']
    if path and (not Path(path).is_absolute() or not Path(path).is_file() or not os.access(path, os.X_OK)):
        raise ValueError('Codexの実行可能なファイルを絶対パスで指定してください。')
    return result


class Settings:
    def __init__(self, path=None, isolated=False):
        self.gnome = None
        self.error = None
        self.values = deepcopy(DEFAULTS)
        self.isolated = isolated
        self.path = path or (Path.home() / 'Library/Application Support/Errand/settings.json'
                             if sys.platform == 'darwin' else
                             Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'errand/settings.json')
        if not isolated:
            try:
                data = json.loads(self.path.read_text())
                # Do not reject a stored path simply because Codex was moved.
                if not isinstance(data, dict):
                    raise ValueError('設定の形式が正しくありません。')
                saved_path = data.get('codex-path', '')
                self.values = validate({**data, 'codex-path': ''})
                self.values['codex-path'] = saved_path if isinstance(saved_path, str) else ''
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                self.error = f'設定を読み込めませんでした: {exc}'

    def get(self, key):
        if self.gnome and key in self.gnome.props.settings_schema.list_keys():
            if key == 'open-errand':
                entries = self.gnome.get_strv(key)
                return entries[0] if entries else ''
            return self.gnome.get_int(key) if key == 'font-size' else self.gnome.get_string(key)
        return deepcopy(self.values[key])

    def save(self, values):
        values = validate(values)
        self._store(values)

    def set_font_size(self, size):
        # Font changes must not reject or modify a previously stored Codex path.
        values = {key: self.get(key) for key in DEFAULTS}
        checked = validate({**values, 'font-size': size, 'codex-path': ''})
        checked['codex-path'] = values['codex-path']
        self._store(checked)

    def _store(self, values):
        if self.error:
            raise ValueError('読み込めなかった既存の設定を保護するため、保存できません。設定ファイルを確認してください。')
        if not self.isolated:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='.settings-', dir=self.path.parent)
            try:
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(values, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.values = values
        if self.gnome:
            self.gnome.delay()
            for key in self.gnome.props.settings_schema.list_keys():
                if key == 'open-errand':
                    self.gnome.set_strv(key, [values[key]] if values[key] else [])
                elif key == 'font-size':
                    self.gnome.set_int(key, values[key])
                elif key == 'codex-path':
                    self.gnome.set_string(key, values[key])
            self.gnome.apply()
