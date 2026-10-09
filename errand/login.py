"""Browser sign-in managed by Codex; Errand never handles account tokens."""

import threading
import tempfile
from urllib.parse import urlsplit

from .protocol import AppServer, RpcError, find_codex, server_command
from .session import Session


def validate_login_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "chatgpt.com"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise RpcError("ログイン先を確認できませんでした。もう一度お試しください。")
    return url


class Login:
    def __init__(self, emit, dispatch=lambda fn: fn(), codex=None, command=None):
        self.emit, self.dispatch = emit, dispatch
        self.codex, self.command = codex, command
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._closed = False
        self._active = False
        self._cancelled = False
        self._server = None
        self._notifications = {}
        self._login_id = None
        self._disconnect_error = None

    def _emit(self, kind, **data):
        def deliver():
            if not self._closed:
                self.emit(kind, data)
            return False
        self.dispatch(deliver)

    def start(self):
        with self._lock:
            if self._closed or self._active:
                return False
            self._active = True
            self._cancelled = False
            self._done.clear()
            self._notifications.clear()
            self._login_id = None
            self._disconnect_error = None
        self._emit("state", active=True)
        threading.Thread(target=self._run, name="errand-login", daemon=True).start()
        return True

    def _event(self, message):
        if message.get("method") == "account/login/completed":
            result = message.get("params", {})
            with self._lock:
                self._notifications[result.get("loginId")] = result
                if self._login_id is not None and result.get("loginId") == self._login_id:
                    self._done.set()

    def _disconnected(self, message):
        self._disconnect_error = message
        self._done.set()

    def _run(self):
        server = None
        try:
            command = self.command or server_command(find_codex(self.codex))
            # The login listener needs no access to user documents.
            with tempfile.TemporaryDirectory(prefix="errand-login-") as directory:
                server = AppServer(command, directory, self._event,
                                   lambda message: server.reject(message["id"]), self._disconnected)
                with self._lock:
                    self._server = server
                    if self._cancelled:
                        return
                server.start()
                Session._initialize(server)
                account = server.request("account/read", {"refreshToken": False}).result(timeout=30)
                if self._cancelled:
                    return
                if account.get("account") is not None or account.get("requiresOpenaiAuth") is not True:
                    self._emit("success")
                    return
                result = server.request("account/login/start", {"type": "chatgpt"}).result(timeout=30)
                url = validate_login_url(result["authUrl"])
                with self._lock:
                    self._login_id = result["loginId"]
                    if self._login_id in self._notifications:
                        self._done.set()
                self._emit("browser", url=url)
                if not self._done.wait(300):
                    raise RpcError("ログインの待ち時間を過ぎました。もう一度お試しください。")
                if self._cancelled:
                    return
                notification = self._notifications.get(self._login_id)
                if not notification or not notification.get("success"):
                    raise RpcError((notification or {}).get("error") or self._disconnect_error
                                   or "ログインできませんでした。もう一度お試しください。")
                self._emit("success")
        except Exception as error:
            if not self._cancelled:
                self._emit("error", message=str(error))
        finally:
            try:
                if server:
                    server.close()
            except Exception as error:
                if not self._cancelled:
                    self._emit("error", message=str(error))
            finally:
                with self._lock:
                    self._server = None
                    self._active = False
                self._emit("state", active=False, cancelled=self._cancelled)

    def cancel(self):
        with self._lock:
            self._cancelled = True
            server = self._server
        self._done.set()
        if server:
            threading.Thread(target=server.close, name="errand-login-cleanup", daemon=True).start()

    def close(self):
        self._closed = True
        self.cancel()
