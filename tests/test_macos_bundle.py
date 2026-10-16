import importlib.util
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('build_macos', Path(__file__).resolve().parents[1] / 'scripts/build_macos.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class MacBundleTests(unittest.TestCase):
    def test_bundle_is_self_contained_source_and_preserves_existing_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            app = builder.build(Path(temp) / 'output with spaces')
            info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleDisplayName'], 'Errand')
            executable = app / 'Contents/MacOS' / info['CFBundleExecutable']
            subprocess.run(['bash', '-n', str(executable)], check=True)
            self.assertTrue(executable.stat().st_mode & 0o111)
            self.assertTrue((app / 'Contents/Resources/errand/ui.py').is_file())
            resources = app / 'Contents/Resources'
            self.assertTrue((resources / 'chat_foundation/gtk/markdown_widget.py').is_file())
            subprocess.run([
                sys.executable, '-B', '-c',
                'from errand.markdown import blocks; '
                'from chat_foundation.core.markdown import blocks as shared; '
                'assert blocks is shared; assert blocks("hello")',
            ], cwd=resources, check=True)
            self.assertEqual((app / 'Contents/Resources/LICENSE').read_bytes(),
                             (builder.ROOT / 'LICENSE').read_bytes())
            self.assertFalse(list(app.rglob('__pycache__')))
            self.assertFalse(list(app.rglob('.codex*')))
            with self.assertRaises(FileExistsError):
                builder.build(app.parent)
