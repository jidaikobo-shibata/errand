"""Exercise native reopen handler ownership without changing any OS settings."""
import ctypes as C
import sys
import unittest

from errand.macos_reopen import EVENT_CLASS, EVENT_ID, EVENT_NOT_HANDLED, HANDLER, MacReopen


@unittest.skipUnless(sys.platform == 'darwin', 'macOS Apple events only')
class MacReopenTests(unittest.TestCase):
    def test_reopen_and_restore_existing_handler(self):
        calls = []
        original = MacReopen(lambda: calls.append('original'))
        replacement = None
        try:
            # A second owner must preserve the original handler and restore it.
            replacement = MacReopen(lambda: calls.append('replacement'))
            handler, refcon = C.c_void_p(), C.c_void_p()

            def dispatch():
                result = original.lib.AEGetEventHandler(
                    EVENT_CLASS, EVENT_ID, C.byref(handler), C.byref(refcon), False,
                )
                self.assertEqual(result, 0)
                return HANDLER(handler.value)(None, None, refcon)

            self.assertEqual(dispatch(), 0)
            self.assertEqual(calls, ['replacement'])
            replacement.close()
            replacement.close()  # Shutdown is idempotent.
            self.assertEqual(dispatch(), 0)
            self.assertEqual(calls, ['replacement', 'original'])
        finally:
            if replacement:
                replacement.close()
            original.close()

    def test_callback_error_does_not_escape_to_native_code(self):
        def fail():
            raise RuntimeError('test callback failure')

        handler = MacReopen(fail)
        try:
            with self.assertLogs(level='ERROR'):
                self.assertEqual(handler.callback(None, None, None), EVENT_NOT_HANDLED)
        finally:
            handler.close()
