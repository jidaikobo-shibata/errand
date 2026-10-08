#!/usr/bin/env python3
"""Build a source-only macOS app. GTK/Python are supplied by Homebrew."""
import argparse
from pathlib import Path
import plistlib
import shutil

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = '''#!/bin/bash
set -eu
resources="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# Use the Python that actually has the Homebrew GI bindings, not Apple's Python.
for python in /opt/homebrew/bin/python3 /usr/local/bin/python3 \\
    /opt/homebrew/opt/python@*/libexec/bin/python3 \\
    /usr/local/opt/python@*/libexec/bin/python3; do
    [ -x "$python" ] || continue
    if "$python" -c 'import gi, cairo; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' >/dev/null 2>&1; then
        exec "$python" "$resources/app.py" "$@"
    fi
done
/usr/bin/osascript -e 'display alert "Errandの実行環境が見つかりません" message "Terminalで brew install gtk4 libadwaita pygobject3 を実行してから、再度開いてください。" as critical'
exit 1
'''


def build(output):
    app = output / 'Errand.app'
    # Refuse to replace existing bundles, including user-managed applications.
    app.mkdir(parents=True, exist_ok=False)
    contents = app / 'Contents'
    resources = contents / 'Resources'
    executables = contents / 'MacOS'
    resources.mkdir(parents=True)
    executables.mkdir()
    shutil.copy2(ROOT / 'app.py', resources / 'app.py')
    shutil.copy2(ROOT / 'LICENSE', resources / 'LICENSE')
    shutil.copytree(ROOT / 'errand', resources / 'errand',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    launcher = executables / 'Errand'
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    with (contents / 'Info.plist').open('wb') as stream:
        plistlib.dump({
            'CFBundleName': 'Errand', 'CFBundleDisplayName': 'Errand',
            'CFBundleIdentifier': 'jp.jidaikobo.Errand',
            'CFBundleExecutable': 'Errand', 'CFBundlePackageType': 'APPL',
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '0.1.0',
            'NSHighResolutionCapable': True,
        }, stream)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/macos',
                        help='出力先（既存のErrand.appは上書きしません）')
    args = parser.parse_args()
    try:
        print(build(args.output).resolve())
    except FileExistsError:
        parser.exit(1, 'Errand.appが既にあります。別の出力先を指定してください。\n')
