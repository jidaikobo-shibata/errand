#!/usr/bin/env python3
"""Build a source-only macOS app. GTK/Python are supplied by Homebrew."""
import argparse
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PROBE = '''#!/bin/bash
set -eu
# Use the Python that actually has the Homebrew GI bindings, not Apple's Python.
for python in /opt/homebrew/bin/python3 /usr/local/bin/python3 \\
    /opt/homebrew/opt/python@*/libexec/bin/python3 \\
    /usr/local/opt/python@*/libexec/bin/python3; do
    [ -x "$python" ] || continue
    if "$python" - "$1" "$python" 2>/dev/null <<'PYTHON'
import json
from pathlib import Path
import platform
import sys
import sysconfig

assert platform.machine() == sys.argv[1]
import gi
import cairo

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

prefix = next(p for p in ("/opt/homebrew", "/usr/local")
              if sys.argv[2].startswith(p + "/"))
library = (Path(sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX"))
           / sysconfig.get_config_var("LDLIBRARY"))
print(json.dumps([sys.executable, str(library), prefix + "/lib"]))
PYTHON
    then
        exit 0
    fi
done
exit 1
'''


def build(output):
    if sys.platform != 'darwin':
        raise ValueError('macOS上でビルドしてください。')
    app = output / 'Errand.app'
    # Refuse to replace existing bundles, including user-managed applications.
    app.mkdir(parents=True, exist_ok=False)
    contents = app / 'Contents'
    resources = contents / 'Resources'
    executables = contents / 'MacOS'
    resources.mkdir(parents=True)
    executables.mkdir()
    shutil.copy2(ROOT / 'app.py', resources / 'app.py')
    shutil.copy2(ROOT / 'scripts/macos_app.py', resources / 'native_startup.py')
    shutil.copy2(ROOT / 'LICENSE', resources / 'LICENSE')
    shutil.copytree(ROOT / 'errand', resources / 'errand',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'chat_foundation', resources / 'chat_foundation',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    launcher = executables / 'Errand'
    (resources / 'find-python').write_text(RUNTIME_PROBE)
    subprocess.run([
        '/usr/bin/clang', '-arch', platform.machine(), '-fobjc-arc',
        '-mmacosx-version-min=11.0', '-framework', 'AppKit',
        str(ROOT / 'scripts/macos_launcher.m'),
        '-o', str(launcher),
    ], check=True)
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
