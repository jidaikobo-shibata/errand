from pathlib import Path
import os
import tempfile
import unittest

from errand.settings import Settings, validate
from errand.macos_shortcut import KEYS


class SettingsTests(unittest.TestCase):
    def test_roundtrip_private_file_and_unknown_keys_are_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested/settings.json'
            settings = Settings(path=path)
            settings.save({'font-size': 18, 'codex-path': '', 'open-errand': '<Super>F5', 'unknown': 'ignored'})
            self.assertEqual(Settings(path=path).get('font-size'), 18)
            self.assertEqual(Settings(path=path).get('open-errand'), '<Super>F5')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('unknown', path.read_text())

    def test_invalid_preferences_preserve_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(path=path)
            settings.save({'font-size': 12})
            before = path.read_bytes()
            for values in ({'font-size': 99}, {'font-size': True}, {'codex-path': 'relative'}, {'open-errand': []}):
                with self.assertRaises(ValueError):
                    settings.save(values)
                self.assertEqual(path.read_bytes(), before)

    def test_corrupt_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('broken input')
            settings = Settings(path=path)
            self.assertTrue(settings.error)
            with self.assertRaises(ValueError):
                settings.save({'font-size': 12})
            self.assertEqual(path.read_text(), 'broken input')

    def test_executable_path_and_missing_stored_path(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'codex test'
            executable.write_text('#!/bin/sh\n')
            executable.chmod(0o700)
            settings = Settings(path=Path(directory) / 'settings.json')
            settings.save({'codex-path': str(executable)})
            executable.unlink()
            self.assertEqual(Settings(path=settings.path).get('codex-path'), str(executable))
            settings.set_font_size(14)
            reopened = Settings(path=settings.path)
            self.assertEqual(reopened.get('font-size'), 14)
            self.assertEqual(reopened.get('codex-path'), str(executable))

    def test_isolated_settings_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(path=path, isolated=True)
            settings.save({'font-size': 14})
            self.assertEqual(settings.get('font-size'), 14)
            self.assertFalse(path.exists())

    def test_shortcut_overrides_persist_and_survive_font_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = Settings(path=path)
            overrides = {'new-tab': '<Control><Alt>n', 'close-tab': ''}
            settings.save({'shortcuts': overrides})
            settings.set_font_size(15)
            self.assertEqual(Settings(path=path).get('shortcuts'), overrides)
            copy = settings.get('shortcuts')
            copy.clear()
            self.assertEqual(settings.get('shortcuts'), overrides)
            with self.assertRaises(ValueError):
                settings.save({'shortcuts': {'unknown-action': '<Control>a'}})

    def test_older_preferences_keep_default_shortcuts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text('{"font-size": 13, "codex-path": "", "open-errand": ""}')
            self.assertEqual(Settings(path=path).get('shortcuts'), {})

    def test_mac_common_key_codes(self):
        self.assertEqual(KEYS['e'], 14)
        self.assertEqual(KEYS['F5'], 96)
        self.assertEqual(KEYS['space'], 49)


class NativeShortcutTests(unittest.TestCase):
    def test_conflict_preserves_old_registration_and_cleanup(self):
        from unittest.mock import patch
        from errand.macos_shortcut import MacShortcut
        class Function:
            def __init__(self, call):
                self.call = call
            def __call__(self, *args):
                return self.call(*args)
        class Library:
            def __init__(self):
                self.rejected = False
                self.removed = []
                self.GetApplicationEventTarget = Function(lambda: 1)
                self.InstallEventHandler = Function(self.install)
                self.RegisterEventHotKey = Function(self.register)
                self.UnregisterEventHotKey = Function(lambda ref: self.removed.append(ref.value) or 0)
                self.RemoveEventHandler = Function(lambda ref: self.removed.append(ref.value) or 0)
            def install(self, target, callback, count, event, data, result):
                result._obj.value = 7
                return 0
            def register(self, key, modifiers, identity, target, flags, result):
                if self.rejected:
                    return -9878
                result._obj.value = 9
                return 0
        lib = Library()
        with patch('errand.macos_shortcut.C.CDLL', return_value=lib):
            shortcut = MacShortcut(lambda: None)
            shortcut.replace(14, 768)
            lib.rejected = True
            with self.assertRaises(ValueError):
                shortcut.replace(96, 256)
            self.assertEqual(shortcut.hotkey.value, 9)
            self.assertEqual(lib.removed, [])
            shortcut.close()
            self.assertEqual(lib.removed, [9, 7])
