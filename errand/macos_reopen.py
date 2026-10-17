"""Route macOS Dock/Finder reopen events to the existing GTK window."""
import ctypes as C
import logging


EVENT_CLASS = int.from_bytes(b'aevt', 'big')
EVENT_ID = int.from_bytes(b'rapp', 'big')
HANDLER_NOT_FOUND = -1717
EVENT_NOT_HANDLED = -1708
HANDLER = C.CFUNCTYPE(C.c_int16, C.c_void_p, C.c_void_p, C.c_void_p)


class MacReopen:
    """Own just this process's reopen handler; preserve GTK's other handlers."""

    def __init__(self, callback):
        self.lib = C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        self.lib.AEGetEventHandler.argtypes = [
            C.c_uint32, C.c_uint32, C.POINTER(C.c_void_p),
            C.POINTER(C.c_void_p), C.c_ubyte,
        ]
        self.lib.AEInstallEventHandler.argtypes = [
            C.c_uint32, C.c_uint32, C.c_void_p, C.c_void_p, C.c_ubyte,
        ]
        self.lib.AERemoveEventHandler.argtypes = [
            C.c_uint32, C.c_uint32, C.c_void_p, C.c_ubyte,
        ]
        for name in ('AEGetEventHandler', 'AEInstallEventHandler', 'AERemoveEventHandler'):
            getattr(self.lib, name).restype = C.c_int16
        self.previous = C.c_void_p()
        self.previous_refcon = C.c_void_p()
        result = self.lib.AEGetEventHandler(
            EVENT_CLASS, EVENT_ID, C.byref(self.previous),
            C.byref(self.previous_refcon), False,
        )
        if result not in (0, HANDLER_NOT_FOUND):
            raise RuntimeError(f'再表示処理の確認に失敗しました（{result}）。')
        self.callback = HANDLER(lambda *_: self._reopen(callback))
        result = self.lib.AEInstallEventHandler(
            EVENT_CLASS, EVENT_ID, self.callback, None, False,
        )
        if result:
            raise RuntimeError(f'再表示処理の登録に失敗しました（{result}）。')
        self.closed = False

    @staticmethod
    def _reopen(callback):
        # Never let a Python exception escape through a native callback.
        try:
            callback()
        except Exception:
            logging.exception('Could not reopen the Errand window')
            return EVENT_NOT_HANDLED
        return 0

    def close(self):
        if self.closed:
            return
        result = self.lib.AERemoveEventHandler(
            EVENT_CLASS, EVENT_ID, self.callback, False,
        )
        if result not in (0, HANDLER_NOT_FOUND):
            raise RuntimeError(f'再表示処理の解除に失敗しました（{result}）。')
        if self.previous:
            result = self.lib.AEInstallEventHandler(
                EVENT_CLASS, EVENT_ID, self.previous, self.previous_refcon, False,
            )
            if result:
                raise RuntimeError(f'再表示処理の復元に失敗しました（{result}）。')
        self.closed = True
