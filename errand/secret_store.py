"""OS credential storage. No plaintext persistence or secret-bearing subprocesses."""
import sys
import threading

from chat_foundation.gemini.credentials import read_api_key


class StoreError(RuntimeError):
    pass


class StoreUnavailable(StoreError):
    pass


class SecretStore:
    def __init__(self, service, *, isolated=False):
        self.service = service + '.gemini-api-key'
        self.isolated = isolated
        self._memory = None
        self._lock = threading.Lock()

    def _backend(self):
        if sys.platform == 'darwin':
            from .macos_secret_store import Keychain
            return Keychain(self.service)
        return Libsecret(self.service)

    def _call(self, operation, key=None):
        with self._lock:
            if self.isolated:
                if operation == 'save':
                    self._memory = key
                elif operation == 'delete':
                    self._memory = None
                return self._memory if operation == 'load' else None
            try:
                backend = self._backend()
                return getattr(backend, operation)(key) if operation == 'save' else getattr(backend, operation)()
            except StoreError:
                raise
            except Exception:
                # OS error text can contain account data; never pass it to widgets/logs.
                raise StoreError('OSの保管庫を利用できませんでした。ロック状態やアクセス許可を確認してください。') from None

    def load(self):
        value = self._call('load')
        return read_api_key(environ={'GEMINI_API_KEY': value}) if value is not None else None

    def save(self, key):
        key = read_api_key(environ={'GEMINI_API_KEY': key})
        self._call('save', key)

    def delete(self):
        self._call('delete')


class Libsecret:
    def __init__(self, service):
        try:
            import gi
            gi.require_version('Secret', '1')
            from gi.repository import Secret
        except (ImportError, ValueError):
            raise StoreUnavailable('OSの保管庫にはlibsecretのPython連携が必要です。平文では保存しません。') from None
        self.api = Secret
        self.schema = Secret.Schema.new('jp.jidaikobo.Errand.Credential', Secret.SchemaFlags.NONE,
                                        {'service': Secret.SchemaAttributeType.STRING})
        self.attributes = {'service': service}

    def load(self):
        return self.api.password_lookup_sync(self.schema, self.attributes, None)

    def save(self, key):
        if not self.api.password_store_sync(self.schema, self.attributes, self.api.COLLECTION_DEFAULT,
                                            'Errand — Gemini APIキー', key, None):
            raise StoreError('APIキーをOSの保管庫に保存できませんでした。')

    def delete(self):
        self.api.password_clear_sync(self.schema, self.attributes, None)
