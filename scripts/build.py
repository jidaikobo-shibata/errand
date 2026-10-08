#!/usr/bin/env python3
"""Build inside dist; never install or enable the extension."""
from pathlib import Path
import shutil
import json
import tempfile
import subprocess
import zipfile

root = Path(__file__).resolve().parent.parent
output = root / "dist"
output.mkdir(exist_ok=True)
uuid = json.loads((root / "metadata.json").read_text())["uuid"]
archive = output / f"{uuid}.zip"
# Compile the local schema too, so this checkout can run directly as an extension.
subprocess.run(["glib-compile-schemas", "--strict", str(root / "schemas")], check=True)
# Fresh staging prevents old builds from contributing removed files or private notes.
with tempfile.TemporaryDirectory(prefix="errand-build-") as staging:
    bundle = Path(staging)
    for name in ("metadata.json", "extension.js", "prefs.js", "shortcutPreferences.js", "app.py", "LICENSE"):
        shutil.copy2(root / name, bundle / name)
    shutil.copytree(root / "schemas", bundle / "schemas")
    shutil.copytree(root / "errand", bundle / "errand",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(bundle))
print(archive)
