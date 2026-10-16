"""UI-independent conversation state and safe defaults for small errands."""

from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading

from .protocol import AppServer, CodexMissing, RpcError, find_codex, server_command
from .product import ERRAND, INSTRUCTIONS, SUMMARY_INSTRUCTIONS
from chat_foundation.codex.connection import initialize


class LoginRequired(RpcError):
    pass


LOGIN_GUIDANCE = "ChatGPTにログインして使い始めてください。「ChatGPTにログイン」を押すとブラウザが開きます。"


class Session:
    """All callbacks are delivered through dispatch, usually GLib.idle_add."""

    def __init__(self, emit, dispatch=lambda fn: fn(), codex=None, command=None, instructions=INSTRUCTIONS,
                 summary_instructions=SUMMARY_INSTRUCTIONS, client_info=None, resolve_codex=None):
        self.emit = emit
        self.dispatch = dispatch
        self.codex = codex
        self.command = command
        self.instructions = instructions
        self.summary_instructions = summary_instructions
        self.client_info = dict(client_info) if client_info is not None else ERRAND.client_info()
        self.resolve_codex = resolve_codex
        self._summary = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="errand")
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._server = None
        self._thread_id = None
        self._turn_id = None
        self._busy = False
        self._interrupt_requested = False
        self._model = None
        self._effort = None
        self.approvals_reviewer = "user"
        self._models = []
        self._loading_models = False
        self._catalogue_server = None
        self._requests = {}
        self._mcp_confirmations = set()
        self._items = {}
        self._scratch = tempfile.TemporaryDirectory(prefix="errand-")

    @property
    def busy(self):
        return self._busy

    @property
    def thread_id(self):
        return self._thread_id

    def _emit(self, kind, **data):
        generation = self._generation

        def deliver():
            if not self._closed and (kind.startswith("models") or generation == self._generation):
                self.emit(kind, data)
            return False

        self.dispatch(deliver)

    def load_models(self):
        if self._closed or self._loading_models:
            return False
        self._loading_models = True
        self._emit("models_state", loading=True)
        self._pool.submit(self._load_models)
        return True

    def _load_models(self):
        server = None
        initialized = False
        try:
            command = self.command or server_command((self.resolve_codex or find_codex)(self.codex))
            server = AppServer(command, self._scratch.name, lambda _: None,
                               lambda message: server.reject(message["id"]), lambda _: None)
            with self._lock:
                if self._closed:
                    return
                self._catalogue_server = server
            server.start()
            self._initialize(server, client_info=self.client_info)
            initialized = True
            account = server.request("account/read", {"refreshToken": False}).result(timeout=30)
            if account.get("requiresOpenaiAuth") is True and account.get("account") is None:
                raise LoginRequired(LOGIN_GUIDANCE)
            # Resolve Codex's actual defaults, including profiles/new-thread rules,
            # without sending a prompt or creating a persistent conversation.
            defaults = server.request("thread/start", {
                "cwd": self._scratch.name, "sandbox": "read-only",
                "approvalPolicy": "on-request", "approvalsReviewer": "user",
                "ephemeral": True,
            }).result(timeout=60)
            default_model = defaults["model"]
            default_effort = defaults.get("reasoningEffort")
            models, seen = [], set()
            cursor = None
            while not self._closed:
                params = {"limit": 100, "includeHidden": True}
                if cursor:
                    params["cursor"] = cursor
                result = server.request("model/list", params).result(timeout=30)
                for model in result["data"]:
                    if ((not model.get("hidden", False) or model.get("model") == default_model) and model.get("model")
                            and "text" in model.get("inputModalities", ["text", "image"])):
                        if not any(entry["model"] == model["model"] for entry in models):
                            models.append(model)
                cursor = result.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise RpcError("モデル一覧のページ取得が繰り返されました。")
                seen.add(cursor)
            if not self._closed:
                if not any(entry["model"] == default_model for entry in models):
                    models.insert(0, {
                        "id": default_model, "model": default_model, "displayName": default_model,
                        "description": "現在のCodex設定で選ばれているモデルです。",
                        "defaultReasoningEffort": default_effort,
                        "supportedReasoningEfforts": ([{"reasoningEffort": default_effort,
                                                       "description": "現在のCodex設定の推論の強さです。"}]
                                                     if default_effort is not None else []),
                    })
                current = next(entry for entry in models if entry["model"] == default_model)
                options = current.setdefault("supportedReasoningEfforts", [])
                if default_effort is not None and not any(option["reasoningEffort"] == default_effort for option in options):
                    options.append({"reasoningEffort": default_effort,
                                    "description": "現在のCodex設定の推論の強さです。"})
                self._models = models
                self._emit("models", models=models, default_model=default_model, default_effort=default_effort)
        except Exception as error:
            if not self._closed:
                self._emit("models_error", message=str(error), login_required=isinstance(error, LoginRequired),
                           setup_required=isinstance(error, CodexMissing) or not initialized)
        finally:
            if server:
                server.close()
            with self._lock:
                if self._catalogue_server is server:
                    self._catalogue_server = None
            self._loading_models = False
            self._emit("models_state", loading=False)

    @staticmethod
    def _initialize(server, timeout=30, cancel=None, client_info=None):
        initialize(server, client_info or ERRAND.client_info(), timeout=timeout, cancel=cancel)

    def send(self, text, model=None, effort=None):
        text = text.strip()
        if not text:
            raise RpcError("お願いを入力してください。")
        if self._busy:
            raise RpcError("実行中です。完了を待つか、中断してください。")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise RpcError("有効なモデルを選んでください。")
        if model:
            entry = next((entry for entry in self._models if entry["model"] == model), None)
            if entry is None:
                raise RpcError("モデル一覧を取得して、利用可能なモデルを選んでください。")
            supported = {option["reasoningEffort"] for option in entry.get("supportedReasoningEfforts", [])}
            if effort is not None and effort not in supported:
                raise RpcError("選択したモデルは、その推論の強さに対応していません。")
        elif effort is not None:
            raise RpcError("推論の強さを指定する場合はモデルを選んでください。")
        self._model, self._effort = model, effort
        self._busy = True
        self._interrupt_requested = False
        self._emit("user", text=text)
        self._emit("state", busy=True, message="Codexに接続しています…")
        generation = self._generation
        self._pool.submit(self._send, generation, text)

    def set_approval_reviewer(self, reviewer):
        if self._busy:
            raise RpcError("承認方法は応答を待ってから変更してください。")
        if reviewer not in {"user", "auto_review"}:
            raise RpcError("未対応の承認方法です。")
        self.approvals_reviewer = reviewer

    def _current(self, generation):
        return not self._closed and generation == self._generation

    def _send(self, generation, text):
        try:
            if not self._server:
                command = self.command or server_command((self.resolve_codex or find_codex)(self.codex))
                cwd = self._scratch.name
                server = AppServer(
                    command, cwd,
                    lambda m: self._event(generation, m),
                    lambda m: self._request(generation, m),
                    lambda error: self._disconnected(generation, error),
                )
                with self._lock:
                    if not self._current(generation):
                        return
                    self._server = server
                server.start()
                self._initialize(server, client_info=self.client_info)
                params = {
                    "cwd": cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": self.approvals_reviewer,
                    "developerInstructions": self.instructions,
                    "ephemeral": True,
                }
                if self._model is not None:
                    params["model"] = self._model
                if self._effort is not None:
                    params["config"] = {"model_reasoning_effort": self._effort}
                result = server.request("thread/start", params).result(timeout=60)
                if not self._current(generation):
                    return
                self._thread_id = result["thread"]["id"]
                self._emit("thread", thread_id=self._thread_id, model=result.get("model", ""),
                           reasoning_effort=result.get("reasoningEffort"))
            if not self._current(generation):
                return
            if self._interrupt_requested:
                self._busy = False
                self._emit("state", busy=False, message="中断しました。")
                return
            # Explicit turn policy prevents inherited extra writable roots/network settings.
            sandbox = {"type": "readOnly", "networkAccess": False}
            params = {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": text}],
                "approvalPolicy": "on-request", "approvalsReviewer": self.approvals_reviewer,
                "sandboxPolicy": sandbox,
            }
            if self._model is not None:
                params["model"] = self._model
            # Explicit null clears a reasoning setting when switching to a
            # model without supported reasoning options on the same thread.
            params["effort"] = self._effort
            result = self._server.request("turn/start", params).result(timeout=60)
            if not self._current(generation):
                return
            turn = result.get("turn", {})
            self._emit("submitted", text=text)
            if turn.get("status") in {"completed", "failed", "interrupted"}:
                self._busy = False
            elif self._busy:
                self._turn_id = turn["id"]
                self._emit("state", busy=True, message="実行中…")
                if self._interrupt_requested:
                    self._interrupt(generation)
        except Exception as error:
            if self._current(generation):
                with self._lock:
                    failed, self._server = self._server, None
                    self._thread_id = self._turn_id = None
                    self._requests.clear()
                    self._items.clear()
                    self._mcp_confirmations.clear()
                    self._generation += 1
                if failed:
                    failed.close()
                self._busy = False
                self._emit("requests_clear")
                self._emit("error", message=str(error))
                self._emit("state", busy=False, message="接続を破棄しました。次の送信で新しい会話として再接続します。")

    def _event(self, generation, message):
        if not self._current(generation):
            return
        method, params = message.get("method"), message.get("params", {})
        if params.get("threadId") and self._thread_id and params["threadId"] != self._thread_id:
            return
        if method == "turn/started":
            self._turn_id = params["turn"]["id"]
            if self._interrupt_requested:
                self._pool.submit(self._interrupt, generation)
        elif method == "item/agentMessage/delta":
            self._emit("delta", item_id=params["itemId"], text=params.get("delta", ""))
        elif method in {"item/started", "item/completed"}:
            item = params.get("item", {})
            item_id = item.get("id", "")
            self._items[item_id] = item
            if item.get("type") == "agentMessage" and method == "item/completed":
                self._emit("assistant", item_id=item_id, text=item.get("text", ""))
            elif item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
                self._emit("progress", item=item, completed=method == "item/completed")
        elif method == "item/commandExecution/outputDelta":
            self._emit("output", item_id=params.get("itemId", ""), text=params.get("delta", ""))
        elif method == "turn/completed":
            turn = params.get("turn", {})
            self._busy = False
            self._turn_id = None
            self._requests.clear()
            self._emit("requests_clear")
            status = turn.get("status", "completed")
            messages = {"completed": "入力を待っています。", "interrupted": "中断しました。", "failed": "作業に失敗しました。"}
            if turn.get("error"):
                self._emit("error", message=turn["error"].get("message", str(turn["error"])))
            self._emit("state", busy=False, message=messages.get(status, status), status=status)
        elif method == "serverRequest/resolved":
            self._requests.pop(params.get("requestId"), None)
            self._emit("request_resolved", request_id=params.get("requestId"))
        elif method == "error":
            error = params.get("error", {})
            self._emit("error", message=error.get("message", str(error)))

    def _request(self, generation, message):
        if not self._current(generation):
            return
        method = message["method"]
        supported = {
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
            "item/tool/requestUserInput", "item/permissions/requestApproval",
            "mcpServer/elicitation/request",
        }
        if method not in supported:
            self._server.reject(message["id"])
            self._emit("error", message=f"未対応の要求を拒否しました: {method}")
            return
        from .elicitation import confirmation_key
        with self._lock:
            if not self._current(generation):
                return
            key = confirmation_key(message.get('params', {})) if method == 'mcpServer/elicitation/request' else None
            if key is not None and key in self._mcp_confirmations:
                self._server.respond(message['id'], {'action': 'accept', 'content': {}})
                return
            self._requests[message["id"]] = message
        params = dict(message.get("params", {}))
        params["item"] = self._items.get(params.get("itemId"), {})
        self._emit("request", request_id=message["id"], method=method, params=params)

    def answer(self, request_id, result, remember=False):
        from .elicitation import confirmation_key
        with self._lock:
            message = self._requests.get(request_id)
            if message is None:
                return False
            key = (confirmation_key(message.get('params', {})) if remember
                   and message['method'] == 'mcpServer/elicitation/request'
                   and result == {'action': 'accept', 'content': {}} else None)
            added = key is not None and key not in self._mcp_confirmations
            if added:
                self._mcp_confirmations.add(key)
            try:
                self._server.respond(request_id, result)
            except Exception:
                if added:
                    self._mcp_confirmations.discard(key)
                raise
            self._requests.pop(request_id, None)
        self._emit("request_resolved", request_id=request_id)
        return True

    def interrupt(self):
        if self._summary:
            self._summary.interrupt()
            return
        if not self._busy:
            return
        self._interrupt_requested = True
        self._emit("state", busy=True, message="中断しています…")
        self._pool.submit(self._interrupt, self._generation)

    def _interrupt(self, generation):
        if self._current(generation) and self._server and self._turn_id:
            try:
                self._server.request("turn/interrupt", {
                    "threadId": self._thread_id, "turnId": self._turn_id,
                }).result(timeout=10)
            except Exception as error:
                if self._current(generation):
                    self._emit("error", message=f"中断に失敗しました: {error}")

    def _disconnected(self, generation, error):
        with self._lock:
            if not self._current(generation):
                return
            old, self._server = self._server, None
            summary, self._summary = self._summary, None
            self._generation += 1
            self._busy = False
            self._thread_id = self._turn_id = None
            self._requests.clear()
            self._items.clear()
            self._mcp_confirmations.clear()
            self._emit("requests_clear")
            self._emit("error", message=error)
            self._emit("state", busy=False, message="接続が終了しました。次の送信で新しい会話として再接続します。")
        if old:
            self._background_cleanup(old.close)
        if summary:
            summary.close(wait=False)

    @staticmethod
    def _background_cleanup(callback):
        # Independent of occupied communication workers; non-daemon so process
        # exit waits for cleanup even after the GTK application has quit.
        thread = threading.Thread(target=callback, name="errand-cleanup", daemon=False)
        thread.start()
        return thread

    def summarize(self, conversation, model=None, effort=None):
        if self._closed or self._busy:
            raise RpcError("応答を待ってから要約してください。")
        if not conversation.strip():
            raise RpcError("要約する会話がありません。")
        generation = self._generation
        answers, errors = {}, []

        def receive(kind, data):
            if not self._current(generation) or self._summary is not summary:
                return
            if kind == "assistant":
                answers[data["item_id"]] = data["text"]
            elif kind == "error":
                errors.append(data["message"])
            elif kind == "request":
                errors.append("要約中の操作要求を拒否しました。")
                summary._server.reject(data["request_id"])
            elif kind == "state" and not data["busy"]:
                self._summary = None
                self._busy = False
                summary.close(wait=False)
                text = "\n\n".join(answers.values()).strip()
                if data.get("status") == "completed" and text and not errors:
                    self._emit("summary", text="以下は前の会話からの引き継ぎです。\n\n" + text)
                    message = "引き継ぎ用の要約をコピーしました。"
                else:
                    message = "要約をコピーしませんでした。" + (errors[-1] if errors else "中断または作成に失敗しました。")
                self._emit("state", busy=False, message=message)

        summary = Session(receive, codex=self.codex, command=self.command, instructions=self.summary_instructions,
                          client_info=self.client_info, resolve_codex=self.resolve_codex)
        summary._models = self._models
        self._summary = summary
        self._busy = True
        self._emit("state", busy=True, message="引き継ぎ用の要約を作成中…")
        try:
            summary.send("次の会話を新しいCodexスレッドへ引き継ぐために要約してください。\n\n" + conversation,
                         model=model, effort=effort)
        except Exception as error:
            self._summary = None
            self._busy = False
            summary.close(wait=False)
            self._emit("state", busy=False, message=str(error))
            raise

    def reset(self):
        if self._busy:
            raise RpcError("実行中のお願いを中断してから、新しいお願いを始めてください。")
        with self._lock:
            self._generation += 1
            old, self._server = self._server, None
            self._thread_id = self._turn_id = None
            self._model = self._effort = None
            self._requests.clear()
            self._items.clear()
            self._mcp_confirmations.clear()
        if old:
            self._background_cleanup(old.close)
        self._emit("reset")

    def close(self, wait=True):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._mcp_confirmations.clear()
            self._generation += 1
            server, self._server = self._server, None
            catalogue, self._catalogue_server = self._catalogue_server, None
            summary, self._summary = self._summary, None
        def cleanup():
            try:
                if summary:
                    summary.close()
                if server:
                    server.close()
                if catalogue:
                    catalogue.close()
            finally:
                self._scratch.cleanup()
        if wait:
            cleanup()
        else:
            self._background_cleanup(cleanup)
        self._pool.shutdown(wait=False, cancel_futures=True)
