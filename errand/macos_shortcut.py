"""macOS global hotkey using the system Carbon API, without extra packages."""
import ctypes as C

# Apple virtual key codes. Limit capture to these keys rather than guessing.
KEYS = dict(zip('asdfhgzxcvbqwerty123465=97-80]ou[ip\nlj\'k;\\,/nm.',
                [0,1,2,3,4,5,6,7,8,9,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47]))
KEYS.update(dict(zip([f'F{i}' for i in range(1,21)],
                    [122,120,99,118,96,97,98,100,101,109,103,111,105,107,113,106,64,79,80,90])))
KEYS.update({'space':49, 'Return':36})


class HotKeyID(C.Structure):
    _fields_ = [('signature', C.c_uint32), ('id', C.c_uint32)]


class EventType(C.Structure):
    _fields_ = [('eventClass', C.c_uint32), ('eventKind', C.c_uint32)]


class MacShortcut:
    def __init__(self, callback):
        self.lib = C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        self.hotkey = C.c_void_p()
        self.handler = C.c_void_p()
        self.callback_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
        self.callback = self.callback_type(lambda *_: (callback(), 0)[1])
        self.lib.GetApplicationEventTarget.restype = C.c_void_p
        self.lib.InstallEventHandler.argtypes = [C.c_void_p, self.callback_type, C.c_uint32,
                                                C.POINTER(EventType), C.c_void_p, C.POINTER(C.c_void_p)]
        self.lib.RegisterEventHotKey.argtypes = [C.c_uint32, C.c_uint32, HotKeyID, C.c_void_p,
                                                C.c_uint32, C.POINTER(C.c_void_p)]
        self.lib.UnregisterEventHotKey.argtypes = [C.c_void_p]
        self.lib.RemoveEventHandler.argtypes = [C.c_void_p]
        event = EventType(int.from_bytes(b'keyb', 'big'), 6)
        result = self.lib.InstallEventHandler(self.lib.GetApplicationEventTarget(), self.callback,
                                             1, C.byref(event), None, C.byref(self.handler))
        if result:
            raise RuntimeError(f'起動キーの準備に失敗しました（{result}）。')

    def replace(self, key, modifiers):
        if key is None:
            if self.hotkey:
                self.lib.UnregisterEventHotKey(self.hotkey)
                self.hotkey = C.c_void_p()
            return
        new = C.c_void_p()
        result = self.lib.RegisterEventHotKey(key, modifiers, HotKeyID(int.from_bytes(b'Ernd', 'big'), 1),
                                             self.lib.GetApplicationEventTarget(), 0, C.byref(new))
        if result:
            raise ValueError('起動キーを登録できません。他のアプリのキーと競合している可能性があります。')
        if self.hotkey:
            self.lib.UnregisterEventHotKey(self.hotkey)
        self.hotkey = new

    def close(self):
        self.replace(None, 0)
        if self.handler:
            self.lib.RemoveEventHandler(self.handler)
            self.handler = C.c_void_p()
