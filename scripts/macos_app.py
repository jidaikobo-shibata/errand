"""Start source-based Errand with the selected Homebrew library directory."""
from pathlib import Path
import runpy
import sys

import gi

gi.Repository.get_default().prepend_library_path(sys.argv.pop(1))
runpy.run_path(str(Path(__file__).with_name('app.py')), run_name='__main__')
