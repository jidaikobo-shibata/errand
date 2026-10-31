"""ACP v1 conversation controller using the shared conversation events."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import uuid

from ..core.process_rpc import JsonRpcProcess, RpcError


class Session:
    startup_message = 'お願いを入力してください。'
    supports_files = True
    supports_approvals = True
    supports_auto_review = False
    supports_reasoning = False
    supports_file_references = True

    def __init__(self, emit, *, dispatch=lambda fn: fn(), launch, instructions, summary_instructions,
                 assistant_name='AI', notice='', default_model=None, process_factory=JsonRpcProcess,
                 file_uri=None, required_mode=None, approval_modes=(), permission_policy=None, mode_notice_filter=None,
                 initial_approval_mode=None, save_approval_mode=None):
        self.emit, self.dispatch = emit, dispatch
        self.launch = launch
        self.instructions, self.summary_instructions = instructions, summary_instructions
        self.assistant_name, self.notice = assistant_name, notice
        self.default_model = default_model
        self.file_uri = file_uri or (lambda path: path.as_uri())
        self.required_mode = required_mode
        self.mode_notice_filter = mode_notice_filter
        self._changing_mode = None
        self.permission_policy = permission_policy
        self._attached_paths = ()
        self.approval_modes = tuple(approval_modes)
        self.approval_mode = initial_approval_mode or required_mode
        self.save_approval_mode = save_approval_mode
        self._active_mode = None
        self._available_modes = set()
        self.process_factory = process_factory
        self.codex = None  # Legacy controller/host compatibility.
        self.approvals_reviewer = 'user'
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='acp')
        self._scratch = tempfile.TemporaryDirectory(prefix='chat-acp-')
        self._generation = 0
        self._closed = self._busy = self._loading_models = False
        self._server = self._session_id = None
        self._models = []
        self._supports_model_selection = False
        self._requests = {}
        self._tools = {}
        self._text = ''
        self._item_id = None
        self._prompt_future = None
        self._interrupt_requested = False
        self._summary = None

    @property
    def busy(self):
        return self._busy

    def _current(self, generation):
        return not self._closed and generation == self._generation

    def _emit(self, kind, generation=None, **data):
        generation = self._generation if generation is None else generation

        def deliver():
            if self._current(generation):
                self.emit(kind, data)
            return False

        self.dispatch(deliver)

    def _connect(self, generation):
        with self._connect_lock:
            if not self._current(generation):
                raise RpcError('接続を終了しました。')
            if self._server and self._session_id:
                return self._server
            spec = self.launch(self._scratch.name, self.instructions)
            server = self.process_factory(
                spec['command'], spec['cwd'], lambda m: self._event(generation, m),
                lambda m: self._request(generation, m), lambda _: self._disconnected(generation),
                environment=spec['environment'], jsonrpc='2.0', label=self.assistant_name)
            with self._lock:
                if not self._current(generation):
                    raise RpcError('接続を終了しました。')
                self._server = server
            server.start()
            result = server.request('initialize', {
                'protocolVersion': 1, 'clientCapabilities': {},
                'clientInfo': spec.get('client_info', {'name': 'chat-foundation', 'version': '1'}),
            }).result(timeout=30)
            if result.get('protocolVersion') != 1:
                raise RpcError('このACPバージョンには対応していません。')
            if spec.get('auth'):
                method = spec['auth'].get('methodId')
                if method not in {entry.get('id') for entry in result.get('authMethods', [])}:
                    raise RpcError('指定した認証方法に対応していません。')
                server.request('authenticate', spec['auth']).result(timeout=30)
            result = server.request('session/new', {'cwd': spec['cwd'], 'mcpServers': []}).result(timeout=60)
            if not isinstance(result.get('sessionId'), str) or not result['sessionId']:
                raise RpcError('会話を開始できませんでした。')
            mode = (result.get('modes') or {}).get('currentModeId')
            if self.required_mode and mode != self.required_mode:
                # Start with the explicitly configured default, never an inherited grant.
                self._set_mode(server, result['sessionId'], self.required_mode)
            self._active_mode = self.required_mode or mode
            self._available_modes = {m['id'] for m in (result.get('modes') or {}).get('availableModes', [])}
            models = result.get('models') or {}
            excluded = set(spec.get('excluded_models', ()))
            entries = []
            for entry in models.get('availableModels') or []:
                model = entry.get('modelId')
                if isinstance(model, str) and model and model not in excluded:
                    entries.append({'model': model, 'displayName': entry.get('name', model),
                                    'description': entry.get('description', ''), 'supportedReasoningEfforts': []})
            current = models.get('currentModelId') or self.default_model or 'default'
            if isinstance(current, str) and current not in excluded and not any(e['model'] == current for e in entries):
                entries.insert(0, {'model': current, 'displayName': current, 'supportedReasoningEfforts': []})
            with self._lock:
                if not self._current(generation):
                    raise RpcError('接続を終了しました。')
                self._session_id = result['sessionId']
                self._models = entries
                self._supports_model_selection = bool(models)
                self.default_model = current
            return server

    def _set_mode(self, server, session_id, mode):
        with self._lock:
            self._changing_mode = mode
        try:
            server.request('session/set_mode', {'sessionId': session_id, 'modeId': mode}).result(timeout=15)
        finally:
            with self._lock:
                self._changing_mode = None

    def load_models(self):
        with self._lock:
            if self._closed or self._loading_models:
                return False
            self._loading_models = True
        self._emit('models_state', loading=True)
        self._pool.submit(self._load_models, self._generation)
        return True

    def _load_models(self, generation):
        try:
            self._connect(generation)
            self._emit('models', generation, models=self._models,
                       default_model=self.default_model, default_effort=None)
        except Exception:
            if self._current(generation):
                self._emit('models_error', generation, message=(
                    'CLIの起動・接続に失敗しました。実行ファイル、APIキー、CLIの対応バージョンを確認してください。'))
                self._dispose_connection()
        finally:
            self._loading_models = False
            self._emit('models_state', loading=False)

    def set_approval_reviewer(self, reviewer):
        # A persisted Codex choice must not silently become an ACP auto-allow mode.
        if reviewer not in ('user', 'auto_review'):
            raise ValueError('未対応の承認方法です。')
        self.approvals_reviewer = 'user'

    def set_approval_mode(self, mode):
        with self._lock:
            if self._closed or self._busy or self._loading_models:
                raise RpcError('作業・接続中は承認方法を変更できません。')
            if mode not in {value for value, _ in self.approval_modes}:
                raise ValueError('未対応の承認方法です。')
            if self._session_id and mode not in self._available_modes:
                raise RpcError('このCLIは指定した承認方法に対応していません。')
            if self.save_approval_mode:
                self.save_approval_mode(mode)
            self.approval_mode = mode

    def send(self, text, model=None, effort=None, files=None):
        text = text.strip()
        # A chat message must not become an agent CLI slash/shell command.
        prompt_text = '利用者からの依頼:\n\n' + text if text.startswith(('/', '$')) else text
        prompt = [{'type': 'text', 'text': prompt_text}]
        paths = []
        for value in files or []:
            path = Path(value)
            if not path.is_absolute() or not path.is_file():
                raise RpcError('対象ファイルが見つかりません。')
            paths.append(str(path))
            prompt.append({'type': 'resource_link', 'uri': self.file_uri(path), 'name': path.name})
        with self._lock:
            if self._closed:
                raise RpcError('この会話は終了しています。')
            if self._busy:
                raise RpcError('実行中です。完了を待つか、中断してください。')
            if not text:
                raise RpcError('お願いを入力してください。')
            model = model or self.default_model
            if not any(entry['model'] == model for entry in self._models) or effort is not None:
                raise RpcError('利用可能なモデルを選んでください。')
            self._busy = True
            self._attached_paths = tuple(paths)
            self._interrupt_requested = False
            self._text = ''
            self._item_id = uuid.uuid4().hex
            self._tools.clear()
            generation = self._generation
        display = text + ('\n\n対象ファイル:\n' + '\n'.join(paths) if paths else '')
        self._emit('user', generation, text=display, started_at=time.monotonic())
        self._emit('state', generation, busy=True, message=f'{self.assistant_name}に接続しています…')
        self._pool.submit(self._send, generation, text, model, prompt)

    def _send(self, generation, text, model, prompt):
        try:
            server = self._connect(generation)
            if not self._current(generation):
                return
            if self.approval_modes and self.approval_mode != self._active_mode:
                if self.approval_mode not in self._available_modes:
                    raise RpcError('このCLIは指定した承認方法に対応していません。')
                self._set_mode(server, self._session_id, self.approval_mode)
                self._active_mode = self.approval_mode
            if self._supports_model_selection:
                server.request('session/set_model', {'sessionId': self._session_id, 'modelId': model}).result(timeout=15)
            with self._lock:
                if not self._current(generation):
                    return
                if self._interrupt_requested:
                    self._finish(generation, 'cancelled')
                    return
                self._prompt_future = server.request('session/prompt', {
                    'sessionId': self._session_id, 'prompt': prompt,
                })
                if self._prompt_future.done():
                    self._prompt_future.result()
            self._emit('submitted', generation, text=text)
            self._emit('state', generation, busy=True, message='実行中…')
            result = self._prompt_future.result(timeout=300)
            self._finish(generation, result.get('stopReason'))
        except Exception:
            if self._current(generation):
                self._dispose_connection()
                if not self._interrupt_requested:
                    self._emit('error', generation, message=(
                        f'{self.assistant_name}の処理に失敗しました。CLIの認証・利用上限を確認してください。'
                        '次の送信は新しい会話として接続します。'))
                self._finish(generation, 'cancelled' if self._interrupt_requested else 'failed')

    def _finish(self, generation, reason):
        with self._lock:
            if not self._current(generation):
                return
            cancelled = self._interrupt_requested or reason == 'cancelled'
            status = 'interrupted' if cancelled else 'completed' if reason == 'end_turn' else 'incomplete'
            if reason == 'failed':
                status = 'failed'
            if self._text:
                self._emit('assistant', generation, item_id=self._item_id, text=self._text)
            self._cancel_permissions()
            self._busy = False
            self._prompt_future = None
            self._emit('requests_clear', generation)
            message = {'completed': '入力を待っています。', 'interrupted': '中断しました。',
                       'failed': '作業に失敗しました。', 'incomplete': '作業が途中で終了しました。'}[status]
            self._emit('state', generation, busy=False, message=message, status=status)

    def _event(self, generation, message):
        with self._lock:
            if not self._current(generation) or message.get('method') != 'session/update':
                return
            params = message.get('params') or {}
            if params.get('sessionId') != self._session_id or not self._busy:
                return
            update = params.get('update') or {}
            kind = update.get('sessionUpdate')
            if kind == 'agent_message_chunk' and not self._interrupt_requested:
                content = update.get('content') or {}
                if content.get('type') == 'text' and isinstance(content.get('text'), str):
                    if (self._changing_mode is not None and self.mode_notice_filter
                            and self.mode_notice_filter(content['text'], self._changing_mode)):
                        return
                    self._text += content['text']
                    self._emit('delta', generation, item_id=self._item_id, text=content['text'])
            elif kind in ('tool_call', 'tool_call_update'):
                call_id = update.get('toolCallId')
                if not isinstance(call_id, str):
                    return
                tool = self._tools.setdefault(call_id, {})
                tool.update(update)
                self._emit('progress', generation, item={'type': tool.get('kind', '作業'),
                           'command': tool.get('title', '作業'), 'details': tool},
                           completed=tool.get('status') in ('completed', 'failed'))

    def _request(self, generation, message):
        with self._lock:
            server = self._server
            if not server:
                return
            params = message.get('params') or {}
            if (not self._current(generation) or params.get('sessionId') != self._session_id
                    or not self._busy or self._interrupt_requested):
                if message.get('method') == 'session/request_permission':
                    server.respond(message['id'], {'outcome': {'outcome': 'cancelled'}})
                else:
                    server.reject(message['id'])
                return
            if message.get('method') != 'session/request_permission':
                server.reject(message['id'])
                return
            options = [option for option in params.get('options', [])
                       if option.get('kind') in ('allow_once', 'reject_once') and isinstance(option.get('optionId'), str)]
            if not any(option['kind'] == 'allow_once' for option in options):
                server.respond(message['id'], {'outcome': {'outcome': 'cancelled'}})
                self._emit('error', generation, message='今回だけ許可できない要求を拒否しました。')
                return
            tool = params.get('toolCall') or {}
            details = {**self._tools.get(tool.get('toolCallId'), {}), **tool}
            # Product-specific exceptions must identify the exact request and current input.
            # Never bypass arbitrary CLI permission requests just because the mode is YOLO.
            if self.permission_policy and self.permission_policy(
                    details, self._attached_paths, self._active_mode):
                option = next(option for option in options if option['kind'] == 'allow_once')
                server.respond(message['id'], {'outcome': {
                    'outcome': 'selected', 'optionId': option['optionId']}})
                self._emit('progress', generation, item={
                    'type': 'read', 'command': details.get('title', '添付ファイルの読み取り'),
                    'details': details}, completed=True)
                return
            self._requests[message['id']] = options
            self._emit('request', generation, request_id=message['id'], method='agent/permission', params={
                'reason': details.get('title', 'この操作を今回だけ許可しますか？'),
                'item': details, 'options': options,
            })

    def answer(self, request_id, result, remember=False):
        with self._lock:
            options = self._requests.get(request_id)
            if options is None or not self._server:
                return False
            if remember:
                raise RpcError('この接続では許可を記憶できません。')
            option = next((option for option in options if option['optionId'] == result.get('option_id')), None)
            cancel = result.get('cancel') is True
            if not cancel and option is None:
                raise RpcError('要求に含まれる今回限りの選択肢を選んでください。')
            outcome = {'outcome': 'cancelled'} if cancel else {'outcome': 'selected', 'optionId': option['optionId']}
            self._server.respond(request_id, {'outcome': outcome})
            self._requests.pop(request_id, None)
        self._emit('request_resolved', request_id=request_id)
        if cancel:
            self.interrupt()
        return True

    def _cancel_permissions(self):
        if self._server:
            for request_id in list(self._requests):
                try:
                    self._server.respond(request_id, {'outcome': {'outcome': 'cancelled'}})
                except Exception:
                    pass
        self._requests.clear()

    def interrupt(self):
        if self._summary:
            self._summary.interrupt()
            return
        with self._lock:
            if not self._busy or self._interrupt_requested:
                return
            self._interrupt_requested = True
            self._cancel_permissions()
            generation = self._generation
            turn_id = self._item_id
        self._emit('requests_clear')
        self._emit('state', busy=True, message='中断しています…')
        self._pool.submit(self._interrupt, generation, turn_id)

    def _interrupt(self, generation, turn_id):
        try:
            with self._lock:
                if (not self._current(generation) or self._item_id != turn_id
                        or not self._busy or not self._interrupt_requested):
                    return
                if self._server and self._session_id:
                    self._server.notify('session/cancel', {'sessionId': self._session_id})
                future = self._prompt_future
            if future:
                future.result(timeout=5)
            else:
                self._dispose_connection(expected_turn=turn_id)
        except Exception:
            if self._current(generation):
                self._dispose_connection(expected_turn=turn_id)

    def _dispose_connection(self, expected_turn=None):
        with self._lock:
            if expected_turn is not None and self._item_id != expected_turn:
                return
            server, self._server = self._server, None
            self._session_id = None
            self._requests.clear()
        if server:
            server.close()

    def _disconnected(self, generation):
        # Pending requests fail on the reader thread; the waiting worker owns cleanup.
        with self._lock:
            if not self._current(generation) or self._busy or self._loading_models or not self._server:
                return
            old, self._server = self._server, None
            self._session_id = None
            self._generation += 1
        self._background_cleanup(old.close)
        self._emit('error', message=f'{self.assistant_name}との接続が切れました。次の送信で新しい会話を開始します。')
        self._emit('state', busy=False, message='再接続を待っています。', status='failed')

    def reset(self):
        if self._busy or self._loading_models:
            raise RpcError('実行中の処理を終えてから、新しいお願いを始めてください。')
        with self._lock:
            self._generation += 1
            self._tools.clear()
            self._text = ''
            old, self._server = self._server, None
            self._session_id = None
        if old:
            self._background_cleanup(old.close)
        self._emit('reset')

    def summarize(self, conversation, model=None, effort=None):
        if self._closed or self._busy or not conversation.strip():
            raise RpcError('会話があり、実行が終わってから要約してください。')
        text, failed = [], []
        generation = self._generation

        def receive(kind, data):
            if not self._current(generation):
                return
            if kind == 'assistant':
                text[:] = [data['text']]
            elif kind == 'error' or kind == 'request':
                failed.append(True)
                if kind == 'request':
                    summary.answer(data['request_id'], {'cancel': True})
            elif kind == 'state' and not data['busy']:
                if data.get('status') == 'completed' and text and not failed:
                    self._emit('summary', generation, text='以下は前の会話からの引き継ぎです。\n\n' + text[0])
                else:
                    self._emit('error', generation, message='要約を作成できませんでした。')
                self._busy = False
                self._summary = None
                summary.close(wait=False)
                self._emit('state', generation, busy=False, message='入力を待っています。')

        def launch(directory, instructions):
            return self.launch(directory, instructions, summary=True)

        summary = Session(receive, dispatch=self.dispatch, launch=launch,
                          instructions=self.summary_instructions, summary_instructions=self.summary_instructions,
                          assistant_name=self.assistant_name, default_model=self.default_model,
                          process_factory=self.process_factory, required_mode=self.required_mode,
                          file_uri=self.file_uri, mode_notice_filter=self.mode_notice_filter)
        summary._models = self._models
        self._summary = summary
        self._busy = True
        self._emit('state', busy=True, message='引き継ぎ用の要約を作成中…')
        try:
            summary.send('次の会話を要約してください。\n\n' + conversation, model, effort)
        except Exception:
            self._summary = None
            self._busy = False
            summary.close(wait=False)
            self._emit('state', busy=False, message='要約を開始できませんでした。')
            raise

    @staticmethod
    def _background_cleanup(callback):
        # Keep interpreter shutdown waiting for process-group termination and cleanup.
        thread = threading.Thread(target=callback, name='acp-cleanup', daemon=False)
        thread.start()
        return thread

    def close(self, wait=True):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._cancel_permissions()
            summary, self._summary = self._summary, None

        def cleanup():
            if summary:
                summary.close()
            self._dispose_connection()
            with self._connect_lock:
                self._scratch.cleanup()

        if wait:
            cleanup()
        else:
            self._background_cleanup(cleanup)
        self._pool.shutdown(wait=False, cancel_futures=True)
