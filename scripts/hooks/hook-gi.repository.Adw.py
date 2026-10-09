"""Collect libadwaita and its introspection dependencies for GTK 4."""
from PyInstaller.utils.hooks.gi import GiModuleInfo

module_info = GiModuleInfo('Adw', '1')
binaries, datas, hiddenimports = module_info.collect_typelib_data()
