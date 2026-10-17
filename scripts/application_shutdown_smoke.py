#!/usr/bin/env python3
"""One failed cleanup must not skip the remaining application resources."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application


class ApplicationShutdownTests(unittest.TestCase):
    def test_every_resource_is_cleaned_even_if_a_previous_one_fails(self):
        for failed in ('reopen', 'runtime', 'shortcut', 'preferences', 'fonts', 'sessions'):
            with self.subTest(failed=failed):
                calls = []
                def action(name):
                    def run(*_):
                        calls.append(name)
                        if name == failed:
                            raise RuntimeError('simulated cleanup failure')
                    return run
                host = SimpleNamespace(mac_reopen=SimpleNamespace(close=action('reopen')),
                    runtime=SimpleNamespace(close=action('runtime')),
                    mac_shortcut=SimpleNamespace(close=action('shortcut')),
                    preferences=SimpleNamespace(destroy=action('preferences')),
                    font_css=object(), window=SimpleNamespace(shutdown_sessions=action('sessions')))
                with patch('errand.ui.Gdk.Display.get_default', return_value=object()), \
                     patch('errand.ui.Gtk.StyleContext.remove_provider_for_display', side_effect=action('fonts')), \
                     self.assertLogs(level='ERROR'):
                    Application.shutdown_session(host, None)
                self.assertEqual(calls, ['reopen', 'runtime', 'shortcut', 'preferences', 'fonts', 'sessions'])

    def test_shutdown_without_optional_resources(self):
        runtime = Mock()
        host = SimpleNamespace(mac_reopen=None, runtime=runtime, mac_shortcut=None,
                               preferences=None, window=None)
        with patch('errand.ui.Gdk.Display.get_default', return_value=None):
            Application.shutdown_session(host, None)
        runtime.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
