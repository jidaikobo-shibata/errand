"""Bidirectional JSONL transport. No shell, credentials, or GTK dependency."""

from concurrent.futures import Future
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time


class RpcError(RuntimeError):
    pass


def find_codex(explicit=None):
    """Desktop launchers do not normally inherit the terminal's PATH."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise RpcError("Codexの実行ファイルを絶対パスで指定してください。")
        return str(path)
    found = shutil.which("codex")
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(directory) / "codex"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidates = list((Path.home() / ".nvm/versions/node").glob("*/bin/codex"))

    def version(path):
        try:
            return tuple(int(part) for part in path.parent.parent.name.lstrip("v").split("."))
        except ValueError:
            return ()

    for candidate in sorted(candidates, key=version, reverse=True):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RpcError("Codexが見つかりません。設定で実行ファイルを指定してください。")


def server_command(codex):
    return [
        codex, "app-server", "--stdio",
        "-c", 'approvals_reviewer="user"',
        "-c", 'approval_policy="on-request"',
        "-c", 'sandbox_mode="read-only"',
        "-c", "agents.enabled=false",
    ]


class AppServer:
    MAX_LINE = 8 * 1024 * 1024

    def __init__(self, command, cwd, on_event, on_request, on_disconnect):
        self.command = list(command)
        self.cwd = str(cwd)
        self.on_event = on_event
        self.on_request = on_request
        self.on_disconnect = on_disconnect
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._pending = {}
        self._next_id = 0
        self._process = None
        self._closed = False
        self._stderr = []
        self._readers = []

    def start(self):
        env = os.environ.copy()
        executable = Path(self.command[0])
        if executable.is_absolute():
            # nvm's Codex wrapper uses /usr/bin/env node. Pair it with its Node.
            env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
        # A nested app-server must not inherit the parent's tool-call identity.
        for key in ["CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_PLUGIN_METRICS_OUTPUT"]:
            env.pop(key, None)
        with self._lock:
            if self._closed:
                raise RpcError("接続を終了しました。")
            self._process = subprocess.Popen(
                self.command, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self._readers = [threading.Thread(target=target, daemon=True)
                             for target in (self._read_stdout, self._read_stderr)]
            for reader in self._readers:
                reader.start()

    def request(self, method, params=None):
        future = Future()
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = future
        try:
            self._write({"id": request_id, "method": method, "params": params or {}})
        except Exception as error:
            with self._lock:
                self._pending.pop(request_id, None)
            future.set_exception(error)
        return future

    def notify(self, method, params=None):
        self._write({"method": method, "params": params or {}})

    def respond(self, request_id, result):
        self._write({"id": request_id, "result": result})

    def reject(self, request_id, message="このクライアントでは対応していない要求です。"):
        self._write({"id": request_id, "error": {"code": -32601, "message": message}})

    def _write(self, message):
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            if self._closed or not self._process or self._process.poll() is not None:
                raise RpcError("Codexとの接続が終了しています。新しいお願いで再接続してください。")
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            except (OSError, ValueError) as error:
                raise RpcError("Codexへの送信に失敗しました。") from error

    def _read_stdout(self):
        error = None
        try:
            while True:
                line = self._process.stdout.readline(self.MAX_LINE + 1)
                if not line:
                    break
                if len(line) > self.MAX_LINE:
                    raise RpcError("Codexからの応答が大きすぎます。")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise RpcError("Codexから不正な形式の応答を受け取りました。")
                if "method" in message:
                    if "id" in message:
                        self.on_request(message)
                    else:
                        self.on_event(message)
                elif "id" in message:
                    with self._lock:
                        future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RpcError(message["error"].get("message", "Codexのエラー")))
                        else:
                            future.set_result(message.get("result", {}))
        except Exception as exc:
            error = exc
        finally:
            if not self._closed:
                error = error or RpcError("Codexとの接続が切れました。新しいお願いで再接続してください。")
                self._fail_pending(error)
                self.on_disconnect(str(error))

    def _read_stderr(self):
        # Keep a bounded diagnostic tail in memory; never persist account data.
        while True:
            line = self._process.stderr.readline(4096)
            if not line:
                break
            with self._lock:
                self._stderr.append(line.decode("utf-8", errors="replace"))
                self._stderr = self._stderr[-20:]

    def _fail_pending(self, error):
        with self._lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    def close(self):
        with self._close_lock:
            self._close()

    def _close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._fail_pending(RpcError("接続を終了しました。"))
        process = self._process
        if not process:
            return
        # Signal the entire private group, even if its original leader exited.
        self._signal_group(signal.SIGTERM)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if not self._signal_group(0):
                break
            time.sleep(0.02)
        self._signal_group(signal.SIGKILL)
        process.wait(timeout=2)
        for reader in self._readers:
            if reader is not threading.current_thread():
                reader.join(timeout=2)
        # Do not block on a stream lock owned by an outstanding reader.
        for stream, reader in zip((process.stdout, process.stderr), self._readers):
            if not reader.is_alive():
                stream.close()
        process.stdin.close()

    def _signal_group(self, sig):
        for attempt in range(2):
            # macOS can report EPERM for a group with only an unreaped zombie.
            # Reap the leader first; its surviving children still need a signal.
            self._process.poll()
            try:
                os.killpg(self._process.pid, sig)
            except ProcessLookupError:
                return False
            except PermissionError as error:
                # macOS can deny signals while exit is still in progress and
                # poll() still reports a running leader. Confirm exit before
                # retrying; never suppress a denial for a live process/group.
                if attempt:
                    raise
                try:
                    self._process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    raise error
            else:
                return True
