from concurrent.futures import CancelledError, Future
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from chat_foundation.codex.connection import initialize
from errand.product import ERRAND
from errand.protocol import CodexMissing, find_codex
from errand.runtime import MANIFEST, RuntimeSetup


class ConnectionTests(unittest.TestCase):
    def test_setup_fallback_keeps_product_root_and_probe_identity(self):
        info = {'name': 'example', 'title': 'Example', 'version': '1'}
        finished = threading.Event()
        events = []
        def emit(kind, data):
            events.append((kind, data))
            if kind == 'state' and not data['active']:
                finished.set()
        def install(root, arch, cancel, report, probe):
            self.assertEqual(root, Path('/tmp/example-runtime'))
            probe('/fixture/codex')
            return '/fixture/codex'
        setup = RuntimeSetup(root=Path('/tmp/example-runtime'), client_info=info,
                             resolve_codex=Mock(side_effect=CodexMissing('missing')))
        setup.subscribe(emit)
        try:
            with patch('errand.runtime.sys.platform', 'darwin'), \
                    patch('errand.runtime.install_runtime', side_effect=install), \
                    patch('errand.runtime.probe_codex') as probe:
                setup.start()
                self.assertTrue(finished.wait(5))
                probe.assert_called_once_with('/fixture/codex', setup._cancel, client_info=info)
            self.assertIn(('ready', {'path': '/fixture/codex'}), events)
        finally:
            setup.close()

    def test_handshake_uses_explicit_product_identity(self):
        server = Mock()
        server.request.return_value = Future()
        server.request.return_value.set_result({})
        info = replace(ERRAND, name='Example', client_name='example').client_info()
        initialize(server, info)
        server.request.assert_called_once_with('initialize', {
            'clientInfo': info, 'capabilities': {'experimentalApi': False}})
        server.notify.assert_called_once_with('initialized')

    def test_cancelled_handshake_does_not_send_initialized(self):
        server = Mock()
        server.request.return_value = Future()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            initialize(server, ERRAND.client_info(), cancel=cancel)
        server.notify.assert_not_called()

    def test_runtime_discovery_uses_the_selected_product_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for product in ('first', 'second'):
                binary = root / product / MANIFEST['version'] / 'x86_64/bin/codex'
                binary.parent.mkdir(parents=True)
                binary.write_text('fixture')
                binary.chmod(0o700)
            with patch('errand.runtime.sys.platform', 'darwin'), \
                    patch('errand.runtime.platform.machine', return_value='x86_64'):
                for product in ('first', 'second'):
                    self.assertEqual(find_codex(runtime_root=root / product),
                                     str(root / product / MANIFEST['version'] / 'x86_64/bin/codex'))
