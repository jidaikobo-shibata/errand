# Native builds: arm64 on Apple Silicon, x86_64 on Intel/Rosetta.
import os
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'app.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'LICENSE'), '.'),
           (str(root / 'errand/runtime_manifest.json'), 'errand')],
    hiddenimports=['gi.repository.Adw', 'gi.repository.PangoCairo'],
    hookspath=[str(root / 'scripts/hooks')],
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'numpy', 'IPython', 'setuptools', 'pip'],
    hooksconfig={'gi': {'module-versions': {'Gtk': '4.0', 'Gdk': '4.0'},
                        'icons': [], 'themes': [], 'languages': ['ja', 'en']}},
    optimize=0,
)
codec = os.environ.get('ERRAND_BUILD_CAIRO_SCRIPT')
if codec:
    a.binaries = [(name, codec if name == 'libcairo-script-interpreter.2.dylib' else source, kind)
                  for name, source, kind in a.binaries if name != 'liblzo2.2.dylib']
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Errand',
          debug=False, strip=False, upx=False, console=False,
          target_arch=os.environ.get('ERRAND_BUILD_ARCH'),
          codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, name='Errand', strip=False, upx=False)
app = BUNDLE(collection, name='Errand.app', bundle_identifier='jp.jidaikobo.Errand',
             info_plist={'CFBundleDisplayName': 'Errand', 'CFBundleShortVersionString': '0.2.0',
                         'CFBundleVersion': '2', 'NSHighResolutionCapable': True,
                         'LSMinimumSystemVersion': '15.0'})
