#!/usr/bin/env python3
"""Register this checkout in the Linux app list and Dock without overwriting files."""
import os
from pathlib import Path
import sys

from gi.repository import GLib

ROOT = Path(__file__).resolve().parent.parent


def desktop_entry():
    entry = GLib.KeyFile()
    for key, value in {
        'Type': 'Application', 'Name': 'Errand',
        'Comment': '小さなお願いを頼むチャット',
        'Icon': str(ROOT / 'errand/icons/hicolor/1024x1024/apps/jp.jidaikobo.Errand.png'),
        'StartupWMClass': 'jp.jidaikobo.Errand',
        'Categories': 'Utility;',
    }.items():
        entry.set_string('Desktop Entry', key, value)
    def quote(argument):
        argument = argument.replace('%', '%%')
        for character in ('\\', '"', '`', '$'):
            argument = argument.replace(character, '\\' + character)
        return '"' + argument + '"'
    entry.set_string('Desktop Entry', 'Exec',
                     ' '.join(quote(arg) for arg in (sys.executable, str(ROOT / 'app.py'))))
    entry.set_boolean('Desktop Entry', 'Terminal', False)
    return entry.to_data()[0]


def install():
    if sys.platform != 'linux':
        raise ValueError('Linux専用です。macOSではアプリをビルドしてください。')
    folder = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'jp.jidaikobo.Errand.desktop'
    content = desktop_entry()
    if target.exists() and target.read_text() == content:
        return target
    with target.open('x', encoding='utf-8') as stream:
        stream.write(content)
    return target


if __name__ == '__main__':
    print(install())
