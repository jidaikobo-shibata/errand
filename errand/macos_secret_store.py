"""Generic-password Keychain adapter through Apple's Security framework."""
import ctypes as C
from contextlib import contextmanager

from .secret_store import StoreError


class Keychain:
    def __init__(self, service):
        self.service = service
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        signatures = {
            'CFStringCreateWithCString': (C.c_void_p, [C.c_void_p, C.c_char_p, C.c_uint32]),
            'CFDataCreate': (C.c_void_p, [C.c_void_p, C.c_void_p, C.c_long]),
            'CFDataGetLength': (C.c_long, [C.c_void_p]),
            'CFDataGetBytePtr': (C.c_void_p, [C.c_void_p]),
            'CFDictionaryCreate': (C.c_void_p, [C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p),
                                               C.c_long, C.c_void_p, C.c_void_p]),
            'CFRelease': (None, [C.c_void_p]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.cf, name)
            function.restype, function.argtypes = result, arguments
        for name, arguments in {
            'SecItemCopyMatching': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemAdd': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemUpdate': [C.c_void_p, C.c_void_p], 'SecItemDelete': [C.c_void_p],
        }.items():
            function = getattr(self.sec, name)
            function.restype, function.argtypes = C.c_int32, arguments

    def constant(self, name):
        library = self.cf if name.startswith('kCF') else self.sec
        return C.c_void_p.in_dll(library, name).value

    @contextmanager
    def query(self, *, secret=None, read=False, attributes_only=False):
        owned = []
        def text(value):
            reference = self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)
            owned.append(reference)
            return reference
        values = {} if attributes_only else {
            'kSecClass': self.constant('kSecClassGenericPassword'),
            'kSecAttrService': text(self.service), 'kSecAttrAccount': text('gemini-api-key')}
        if secret is not None:
            data = secret.encode('utf-8')
            reference = self.cf.CFDataCreate(None, data, len(data))
            owned.append(reference)
            values['kSecValueData'] = reference
        if read:
            values['kSecReturnData'] = self.constant('kCFBooleanTrue')
            values['kSecMatchLimit'] = self.constant('kSecMatchLimitOne')
        size = len(values)
        keys = (C.c_void_p * size)(*(self.constant(key) for key in values))
        refs = (C.c_void_p * size)(*values.values())
        # Values remain alive for the entire synchronous call; no retain callbacks needed.
        dictionary = self.cf.CFDictionaryCreate(None, keys, refs, size, None, None)
        try:
            yield dictionary
        finally:
            self.cf.CFRelease(dictionary)
            for reference in owned:
                self.cf.CFRelease(reference)

    @staticmethod
    def check(status):
        if status:
            raise StoreError('キーチェーンへのアクセスに失敗しました。ロック状態やアクセス許可を確認してください。')

    def load(self):
        result = C.c_void_p()
        with self.query(read=True) as query:
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
        if status == -25300:  # errSecItemNotFound
            return None
        self.check(status)
        try:
            return C.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result)).decode('utf-8')
        finally:
            self.cf.CFRelease(result)

    def save(self, key):
        with self.query(secret=key) as query:
            status = self.sec.SecItemAdd(query, None)
        if status == -25299:  # errSecDuplicateItem: update without deleting the old credential.
            with self.query() as query, self.query(secret=key, attributes_only=True) as attributes:
                status = self.sec.SecItemUpdate(query, attributes)
        self.check(status)

    def delete(self):
        with self.query() as query:
            status = self.sec.SecItemDelete(query)
        if status != -25300:
            self.check(status)
