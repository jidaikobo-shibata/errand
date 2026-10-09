"""User-triggered, verified Codex setup; no shell or global installation."""

import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import sys
import tarfile
import tempfile
import threading
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

MANIFEST = json.loads(Path(__file__).with_name('runtime_manifest.json').read_text())
SETUP_DESCRIPTION = (
    'Errandの動作に必要なプログラム（Codex）を確認し、未導入の場合は'
    'ダウンロードしてこのMacに保存します。準備が終わったら、ChatGPTへのログインに進みます。'
)


class SetupCancelled(Exception):
    pass


class SetupError(Exception):
    pass


def runtime_root():
    return Path.home() / 'Library/Application Support/Errand/runtimes'


def runtime_directory(root=None, arch=None):
    return (root or runtime_root()) / MANIFEST['version'] / (arch or platform.machine())


def managed_codex():
    if sys.platform != 'darwin':
        return None
    binary = runtime_directory() / 'bin/codex'
    return str(binary) if binary.is_file() and os.access(binary, os.X_OK) else None


def validate_download_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise SetupError('配布元を確認できなかったため、準備を中止しました。')


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def open_download(url):
    validate_download_url(url)
    # Use macOS's certificate bundle, never a Homebrew certificate path.
    context = ssl.create_default_context(cafile='/etc/ssl/cert.pem' if sys.platform == 'darwin' else None)
    opener = build_opener(OfficialRedirects(), HTTPSHandler(context=context))
    return opener.open(Request(url, headers={'User-Agent': 'Errand-runtime-setup'}), timeout=30)


def check_cancelled(cancel):
    if cancel.is_set():
        raise SetupCancelled()


def download_archive(asset, destination, cancel, progress):
    checksum = hashlib.sha256()
    received = 0
    validate_download_url(asset['url'])
    with open_download(asset['url']) as response, destination.open('wb') as stream:
        validate_download_url(response.geturl())
        while True:
            check_cancelled(cancel)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > asset['size']:
                raise SetupError('配布物のサイズが一致しないため、準備を中止しました。')
            checksum.update(chunk)
            stream.write(chunk)
            progress('ダウンロード中', received / asset['size'])
    check_cancelled(cancel)
    if received != asset['size'] or checksum.hexdigest() != asset['sha256']:
        raise SetupError('配布物を検証できなかったため、準備を中止しました。もう一度お試しください。')


def extract_package(archive, destination, cancel):
    total = 0
    with tarfile.open(archive, mode='r:gz') as stream:
        members = stream.getmembers()
        if len(members) > 10000:
            raise SetupError('配布物の内容を確認できませんでした。')
        for member in members:
            check_cancelled(cancel)
            total += member.size
            if total > 1024 * 1024 * 1024:
                raise SetupError('配布物の展開サイズが上限を超えています。')
            stream.extract(member, destination, filter='data')


def probe_codex(binary, cancel=None):
    """Check the actual app-server handshake, without an account or a prompt."""
    from .protocol import AppServer, server_command
    from .session import Session
    with tempfile.TemporaryDirectory(prefix='errand-setup-check-') as directory:
        server = AppServer(server_command(str(binary)), directory, lambda _: None,
                           lambda message: server.reject(message['id']), lambda _: None)
        try:
            server.start()
            # A freshly downloaded Intel binary can need over a minute for
            # Rosetta's first translation. The GUI remains cancellable.
            Session._initialize(server, timeout=180, cancel=cancel)
        finally:
            server.close()


def install_runtime(root, arch, cancel, progress, probe=None):
    probe = probe or (lambda binary: probe_codex(binary, cancel))
    if arch not in MANIFEST['macos']:
        raise SetupError('このMacの種類に対応する配布物を確認できませんでした。')
    asset = MANIFEST['macos'][arch]
    target = runtime_directory(root, arch)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=root) as temporary:
        temporary = Path(temporary)
        archive = temporary / 'codex.tar.gz'
        progress('ダウンロード中', 0)
        download_archive(asset, archive, cancel, progress)
        progress('ダウンロードしたプログラムを検証・展開しています…', None)
        extracted = temporary / 'package'
        extracted.mkdir()
        extract_package(archive, extracted, cancel)
        # The official archive may wrap the package in one top-level directory.
        binaries = [extracted / 'bin/codex'] + list(extracted.glob('*/bin/codex'))
        binaries = [binary for binary in binaries if binary.is_file() and os.access(binary, os.X_OK)]
        if len(binaries) != 1:
            raise SetupError('必要なプログラムが配布物にありません。')
        package = binaries[0].parent.parent
        check_cancelled(cancel)
        progress('動作確認中…', None)
        probe(binaries[0])
        check_cancelled(cancel)
        # Never overwrite an existing installation. Another window/process may
        # have finished the same installation while this download was running.
        if target.exists():
            existing = target / 'bin/codex'
            probe(existing)
            return str(existing)
        (package / 'ERRAND_INSTALL.json').write_text(json.dumps(
            {'version': MANIFEST['version'], 'architecture': arch, 'sha256': asset['sha256']}) + '\n')
        package.rename(target)
    return str(target / 'bin/codex')


class RuntimeSetup:
    """One shared setup operation for all tabs; callbacks run on the UI thread."""
    def __init__(self, dispatch=lambda fn: fn(), root=None, arch=None):
        self.dispatch = dispatch
        self.root = root or runtime_root()
        self.arch = arch or platform.machine()
        self._lock = threading.Lock()
        self._listeners = []
        self._cancel = threading.Event()
        self._active = False
        self._closed = False
        self._progress = ('動作環境を確認しています…', None)

    @property
    def active(self):
        with self._lock:
            return self._active

    @property
    def progress(self):
        with self._lock:
            return self._progress

    def subscribe(self, listener):
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _emit(self, kind, **data):
        def deliver():
            with self._lock:
                listeners = list(self._listeners) if not self._closed else []
            for listener in listeners:
                listener(kind, data)
            return False
        self.dispatch(deliver)

    def _report(self, message, fraction):
        check_cancelled(self._cancel)
        with self._lock:
            self._progress = (message, fraction)
        self._emit('progress', message=message, fraction=fraction)

    def start(self):
        with self._lock:
            if self._closed or self._active:
                return False
            self._active = True
            self._cancel.clear()
        self._emit('state', active=True)
        threading.Thread(target=self._run, name='errand-runtime-setup', daemon=True).start()
        return True

    def _run(self):
        try:
            from .protocol import find_codex, RpcError
            self._report('動作環境を確認しています…', None)
            try:
                existing = find_codex()
                check_cancelled(self._cancel)
                probe_codex(existing, self._cancel)
            except (RpcError, OSError, TimeoutError):
                if sys.platform != 'darwin':
                    raise SetupError('この準備機能はMac向けです。Codexを導入してから再度お試しください。')
                existing = install_runtime(self.root, self.arch, self._cancel, self._report)
            check_cancelled(self._cancel)
            self._emit('ready', path=existing)
        except SetupCancelled:
            self._emit('cancelled')
        except Exception as error:
            if self._cancel.is_set():
                self._emit('cancelled')
            else:
                message = str(error) if isinstance(error, SetupError) else (
                    '動作環境を準備できませんでした。時間をおいて、もう一度お試しください。')
                self._emit('error', message=message)
        finally:
            with self._lock:
                self._active = False
            self._emit('state', active=False)

    def cancel(self):
        self._cancel.set()

    def close(self):
        with self._lock:
            self._closed = True
        self.cancel()
