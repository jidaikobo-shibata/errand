from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from errand.secret_store import SecretStore, StoreError, StoreUnavailable
from errand.gemini_cli import Launcher


class SecretTests(unittest.TestCase):
    def test_isolated_store_never_opens_os_store(self):
        store = SecretStore('test', isolated=True)
        with patch.object(store, '_backend', side_effect=AssertionError('OS touched')):
            self.assertIsNone(store.load())
            store.save('synthetic.key')
            self.assertEqual(store.load(), 'synthetic.key')
            store.save('replacement')
            self.assertEqual(store.load(), 'replacement')
            store.delete()
            self.assertIsNone(store.load())

    def test_blank_file_path_reads_default_file_without_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.gemini').mkdir()
            (home / 'runtime').mkdir()
            (home / '.gemini/.env').write_text('GEMINI_API_KEY=default-file-key\n')
            with patch('errand.gemini_cli.find_gemini', return_value='/usr/bin/gemini'), \
                    patch('errand.gemini_cli.Path.home', return_value=home), \
                    patch.dict('os.environ', {}, clear=True):
                spec = Launcher(credentials_path='')(home / 'runtime', 'instructions')
            self.assertEqual(spec['auth']['_meta']['api-key'], 'default-file-key')

    def test_errors_are_redacted_and_invalid_keys_not_stored(self):
        store = SecretStore('test')
        backend = Mock()
        with patch.object(store, '_backend', return_value=backend):
            for key in ('', 'contains\nnewline', 'contains space'):
                with self.assertRaises(ValueError):
                    store.save(key)
            backend.save.assert_not_called()
            backend.save.side_effect = RuntimeError('secret-key-and-account')
            with self.assertRaises(StoreError) as raised:
                store.save('synthetic-key')
            self.assertNotIn('secret-key-and-account', str(raised.exception))

    def test_launcher_prefers_store_and_defaults_blank_path(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('errand.gemini_cli.find_gemini', return_value='/usr/bin/gemini'), \
                patch('errand.gemini_cli.read_api_key', return_value='file-key') as read:
            provider = Mock(return_value='stored-key')
            launcher = Launcher(key_provider=provider)
            spec = launcher(directory, 'instructions')
            self.assertEqual(spec['auth']['_meta']['api-key'], 'stored-key')
            read.assert_not_called()
            self.assertEqual((Path(directory) / 'work/.env').read_text(), '')
            provider.return_value = None
            launcher(directory, 'instructions')
            read.assert_called_with(Path.home() / '.gemini/.env')
            provider.side_effect = StoreError('locked')
            read.reset_mock()
            with self.assertRaises(StoreError):
                launcher(directory, 'instructions')
            read.assert_not_called()
            provider.side_effect = StoreUnavailable('no libsecret')
            launcher(directory, 'instructions')
            read.assert_called_once()

    def test_mac_keychain_update_and_delete_are_scoped(self):
        from errand.macos_secret_store import Keychain
        backend = Keychain.__new__(Keychain)
        backend.sec = Mock()
        queries = []
        @contextmanager
        def query(**kwargs):
            queries.append(kwargs)
            yield len(queries)
        backend.query = query
        backend.sec.SecItemAdd.return_value = -25299
        backend.sec.SecItemUpdate.return_value = 0
        backend.save('synthetic')
        self.assertEqual(queries, [{'secret': 'synthetic'}, {}, {'secret': 'synthetic', 'attributes_only': True}])
        backend.sec.SecItemDelete.assert_not_called()
        backend.sec.SecItemDelete.return_value = -25300
        backend.delete()
        backend.sec.SecItemCopyMatching.return_value = -25300
        self.assertIsNone(backend.load())
        with self.assertRaises(StoreError):
            backend.check(-25293)

    def test_libsecret_service_scoping_and_default_collection(self):
        from errand.secret_store import Libsecret
        backend = Libsecret.__new__(Libsecret)
        backend.api = Mock(COLLECTION_DEFAULT='default')
        backend.schema, backend.attributes = object(), {'service': 'product.gemini-api-key'}
        backend.api.password_store_sync.return_value = True
        backend.save('synthetic')
        args = backend.api.password_store_sync.call_args.args
        self.assertEqual(args[1:3], (backend.attributes, 'default'))
        self.assertEqual(args[4], 'synthetic')
        backend.api.password_store_sync.return_value = False
        with self.assertRaises(StoreError):
            backend.save('synthetic')
