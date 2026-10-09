import hashlib
import io
from concurrent.futures import CancelledError
from pathlib import Path
import tarfile
import tempfile
import threading
import sys
import time
import unittest
from unittest.mock import patch

from errand.protocol import CodexMissing, find_codex
from errand.runtime import (MANIFEST, RuntimeSetup, SetupCancelled, SetupError,
                            download_archive, extract_package, install_runtime,
                            probe_codex, validate_download_url)


class Response(io.BytesIO):
    def geturl(self):
        return 'https://release-assets.githubusercontent.com/verified'


def fixture_archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name in ('bin/codex', 'bin/codex-code-mode-host'):
            content = b'fixture executable'
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o755
            archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


class RuntimeTests(unittest.TestCase):
    def asset(self, content):
        return {'url': 'https://github.com/openai/codex/releases/download/test/package.tar.gz',
                'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}

    def test_download_requires_official_https_and_safe_redirects(self):
        for url in ('http://github.com/file', 'https://github.com.evil.test/file',
                    'https://attacker@github.com/file', 'https://github.com:444/file'):
            with self.subTest(url=url), self.assertRaises(SetupError):
                validate_download_url(url)

    def test_corrupt_download_is_not_extracted_or_installed(self):
        content = fixture_archive()
        asset = self.asset(content)
        asset['sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(MANIFEST['macos'], {'arm64': asset}), \
                patch('errand.runtime.open_download', return_value=Response(content)):
            root = Path(temporary)
            with self.assertRaises(SetupError):
                install_runtime(root, 'arm64', threading.Event(), lambda *_: None)
            self.assertFalse(list(root.glob('.prepare-*')))
            self.assertFalse((root / MANIFEST['version'] / 'arm64').exists())

    def test_oversized_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch('errand.runtime.open_download', return_value=Response(b'large')):
            with self.assertRaises(SetupError):
                download_archive(self.asset(b's'), Path(temporary) / 'download',
                                 threading.Event(), lambda *_: None)

    def test_cancelled_download_leaves_no_installation(self):
        content = fixture_archive()
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(MANIFEST['macos'], {'arm64': self.asset(content)}), \
                patch('errand.runtime.open_download', return_value=Response(content)):
            root = Path(temporary)
            with self.assertRaises(SetupCancelled):
                install_runtime(root, 'arm64', cancel, lambda *_: cancel.set())
            self.assertFalse(list(root.glob('.prepare-*')))
            self.assertFalse((root / MANIFEST['version'] / 'arm64').exists())

    def test_archive_traversal_cannot_escape_installation(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo('../../outside')
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'bad.tar.gz'
            archive.write_bytes(buffer.getvalue())
            with self.assertRaises(tarfile.FilterError):
                extract_package(archive, root / 'stage', threading.Event())
            self.assertFalse((root / 'outside').exists())

    def test_success_is_probed_before_becoming_visible_for_each_cpu(self):
        content = fixture_archive()
        for arch in ('arm64', 'x86_64'):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as temporary, \
                    patch.dict(MANIFEST['macos'], {arch: self.asset(content)}), \
                    patch('errand.runtime.open_download', return_value=Response(content)):
                root, seen = Path(temporary), []
                def probe(binary):
                    self.assertTrue(binary.is_file())
                    self.assertFalse((root / MANIFEST['version'] / arch).exists())
                    seen.append(binary)
                result = install_runtime(root, arch, threading.Event(), lambda *_: None, probe)
                self.assertTrue(Path(result).is_file())
                self.assertEqual(len(seen), 1)
                self.assertTrue(Path(result).parent.parent.joinpath('ERRAND_INSTALL.json').exists())
                self.assertFalse(list(root.glob('.prepare-*')))

    def test_failed_probe_does_not_publish_installation(self):
        content = fixture_archive()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(MANIFEST['macos'], {'arm64': self.asset(content)}), \
                patch('errand.runtime.open_download', return_value=Response(content)):
            root = Path(temporary)
            with self.assertRaises(OSError):
                install_runtime(root, 'arm64', threading.Event(), lambda *_: None,
                                lambda _: (_ for _ in ()).throw(OSError('incompatible')))
            self.assertFalse((root / MANIFEST['version'] / 'arm64').exists())

    def test_controller_reuses_existing_runtime_without_download(self):
        events, finished = [], threading.Event()
        def emit(kind, data):
            events.append((kind, data))
            if kind == 'state' and not data['active']:
                finished.set()
        setup = RuntimeSetup()
        setup.subscribe(emit)
        with patch('errand.protocol.find_codex', return_value='/existing/codex'), \
                patch('errand.runtime.probe_codex'), patch('errand.runtime.install_runtime') as install:
            self.assertTrue(setup.start())
            self.assertTrue(finished.wait(3))
            install.assert_not_called()
        self.assertIn(('ready', {'path': '/existing/codex'}), events)
        setup.close()

    def test_cancellation_during_probe_has_no_success_and_blocks_duplicate_start(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        events = []
        def probe(_, cancel=None):
            started.set()
            release.wait(3)
        def emit(kind, data):
            events.append(kind)
            if kind == 'state' and not data['active']:
                finished.set()
        setup = RuntimeSetup()
        setup.subscribe(emit)
        with patch('errand.protocol.find_codex', return_value='/existing/codex'), \
                patch('errand.runtime.probe_codex', side_effect=probe):
            setup.start()
            self.assertTrue(started.wait(3))
            self.assertFalse(setup.start())
            setup.cancel()
            release.set()
            self.assertTrue(finished.wait(3))
        self.assertIn('cancelled', events)
        self.assertNotIn('ready', events)
        self.assertNotIn('error', events)
        setup.close()

    def test_unresponsive_probe_can_be_cancelled_and_reaped_promptly(self):
        cancel = threading.Event()
        timer = threading.Timer(.2, cancel.set)
        command = [sys.executable, '-u', '-c',
                   'import sys, time; sys.stdin.readline(); time.sleep(60)']
        timer.start()
        started = time.monotonic()
        try:
            with patch('errand.protocol.server_command', return_value=command):
                with self.assertRaises(CancelledError):
                    probe_codex('/fixture/codex', cancel)
            self.assertLess(time.monotonic() - started, 5)
        finally:
            timer.cancel()

    def test_controller_failure_allows_retry_and_closing_suppresses_callbacks(self):
        events, finished = [], threading.Event()
        def emit(kind, data):
            events.append(kind)
            if kind == 'state' and not data['active']:
                finished.set()
        setup = RuntimeSetup()
        setup.subscribe(emit)
        with patch('errand.protocol.find_codex', side_effect=CodexMissing('missing')), \
                patch('errand.runtime.sys.platform', 'darwin'), \
                patch('errand.runtime.install_runtime', side_effect=SetupError('retry')):
            setup.start()
            self.assertTrue(finished.wait(3))
            self.assertIn('error', events)
            finished.clear()
            self.assertTrue(setup.start())
            self.assertTrue(finished.wait(3))
        setup.close()
        count = len(events)
        setup._emit('error', message='late')
        self.assertEqual(len(events), count)
        self.assertFalse(setup.start())

    def test_managed_installation_and_standalone_cli_can_be_discovered(self):
        with tempfile.TemporaryDirectory() as temporary, patch('errand.protocol.Path.home', return_value=Path(temporary)), \
                patch('errand.protocol.shutil.which', return_value=None), \
                patch('errand.runtime.sys.platform', 'darwin'), patch('errand.runtime.platform.machine', return_value='x86_64'):
            binary = Path(temporary) / 'Library/Application Support/Errand/runtimes' / MANIFEST['version'] / 'x86_64/bin/codex'
            binary.parent.mkdir(parents=True)
            binary.write_text('fixture')
            binary.chmod(0o755)
            self.assertEqual(find_codex(), str(binary))
            binary.unlink()
            standalone = Path(temporary) / '.local/bin/codex'
            standalone.parent.mkdir(parents=True)
            standalone.write_text('fixture')
            standalone.chmod(0o755)
            self.assertEqual(find_codex(), str(standalone))
