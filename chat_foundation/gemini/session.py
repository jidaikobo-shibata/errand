"""Text-only Gemini controller; no local tools, retries or credential persistence."""
from copy import deepcopy
import json
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from .credentials import read_api_key
from .network import HTTPSHandler


DEFAULT_MODEL = 'gemini-3.5-flash-lite'
MODELS = (DEFAULT_MODEL, 'gemini-3.8-flash')
MAX_RESPONSE_BYTES = 2_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward an API key to another origin.
        return None


def _stream_events(response, cancel, started):
    """Parse SSE independently of network/UTF-8 chunk boundaries."""
    pending = bytearray()
    data_lines = []
    total = 0
    seen = False
    while not cancel.is_set():
        if time.monotonic() - started >= 120:
            raise TimeoutError()
        chunk = response.read1(65536)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError('Geminiの応答が大きすぎるため受信を中止しました。')
        pending.extend(chunk)
        if not chunk:
            pending.extend(b'\n\n')
        while b'\n' in pending:
            line, _, remainder = pending.partition(b'\n')
            pending = bytearray(remainder)
            line = line.removesuffix(b'\r')
            if line.startswith(b'data:'):
                data_lines.append(line[5:].removeprefix(b' '))
            elif not line and data_lines:
                raw = b'\n'.join(data_lines)
                data_lines.clear()
                if raw.strip() == b'[DONE]':
                    return
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError('Geminiの応答を読み取れませんでした。')
                seen = True
                if cancel.is_set():
                    return
                yield event
        if not chunk:
            if not seen:
                raise ValueError('Geminiの応答を読み取れませんでした。')
            return


def _append_parts(parts, incoming):
    for part in incoming:
        part = deepcopy(part)
        text_fields = {'text', 'thought', 'thoughtSignature'}
        if (parts and 'text' in parts[-1] and set(parts[-1]) <= text_fields and set(part) <= text_fields
                and bool(parts[-1].get('thought')) == bool(part.get('thought'))
                and not (parts[-1].get('thoughtSignature') and part.get('thoughtSignature'))):
            parts[-1]['text'] += part.pop('text', '')
            parts[-1].update(part)
        else:
            parts.append(part)


def generate(model, contents, instructions, key, cancel, on_chunk=None):
    if cancel.is_set():
        return None
    request = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse',
        data=json.dumps({'contents': contents,
                         'systemInstruction': {'parts': [{'text': instructions}]}}).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                 'x-goog-api-key': key}, method='POST')
    try:
        started = time.monotonic()
        context = ssl.create_default_context(cafile='/etc/ssl/cert.pem' if sys.platform == 'darwin' else None)
        opener = urllib.request.build_opener(_NoRedirect(), HTTPSHandler(context=context))
        merged = {'candidates': []}
        if cancel.is_set():
            return None
        with opener.open(request, timeout=30) as response:
            for chunk in _stream_events(response, cancel, started):
                if 'error' in chunk:
                    raise ValueError('Geminiのストリーミング受信に失敗しました。自動再送はしません。')
                merged.update({name: value for name, value in chunk.items() if name != 'candidates'})
                for candidate in chunk.get('candidates') or []:
                    if candidate.get('index', 0) != 0:
                        continue
                    if not merged['candidates']:
                        merged['candidates'].append({'content': {'role': 'model', 'parts': []}})
                    target = merged['candidates'][0]
                    target.update({name: value for name, value in candidate.items() if name != 'content'})
                    _append_parts(target['content']['parts'], (candidate.get('content') or {}).get('parts') or [])
                if on_chunk:
                    on_chunk(chunk)
        if cancel.is_set():
            return None
        return merged
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        messages = {
            400: 'リクエストまたはモデルの設定を確認してください。',
            401: 'APIキーを確認してください。',
            403: 'APIキー・プロジェクトの利用権限を確認してください。',
            402: 'Google AI Studioで課金状態を確認してください。',
            404: '指定モデルが利用できません。',
            429: '利用上限に達しました。時間をおいてお試しください。',
            503: 'Google側が混雑しています。時間をおいてお試しください。',
        }
        raise ValueError(f'Gemini API（HTTP {code}）: ' + messages.get(code, '応答に失敗しました。')) from None
    except (TimeoutError, socket.timeout):
        raise ValueError('Geminiの通信が時間切れになりました（通信待ち30秒・全体約120秒）。自動再送はしません。') from None
    except urllib.error.URLError:
        raise ValueError('Geminiに接続できませんでした。ネットワークを確認してください。') from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('Geminiの応答を読み取れませんでした。') from None


class Session:
    assistant_name = 'Gemini'
    supports_files = False
    supports_approvals = False
    supports_reasoning = False
    notice = ('Gemini：文章の会話・要約に対応します。ファイル操作・ウェブ検索・MCPは使えません。'
              'APIの利用枠・料金・データの取り扱いはGoogle AI Studioの設定・規約に従います。')

    def __init__(self, emit, *, dispatch=lambda fn: fn(), instructions, summary_instructions,
                 credentials_path=None, transport=generate, key_reader=read_api_key):
        self.emit, self.dispatch = emit, dispatch
        self.instructions, self.summary_instructions = instructions, summary_instructions
        self.credentials_path = credentials_path
        self._transport, self._key_reader = transport, key_reader
        self.codex = None  # Compatibility with the existing host contract.
        self.approvals_reviewer = 'user'
        self._history = []
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = self._busy = False
        self._worker = None
        self._cancel = threading.Event()

    @property
    def busy(self):
        return self._busy

    def _emit(self, kind, generation=None, **data):
        generation = self._generation if generation is None else generation

        def deliver():
            if not self._closed and generation == self._generation:
                self.emit(kind, data)
            return False

        self.dispatch(deliver)

    def load_models(self):
        if self._closed:
            return False
        self._emit('models_state', loading=True)
        try:
            # Read locally only: opening a tab never sends a request to Google.
            self._key_reader(self.credentials_path)
            models = [{'model': model, 'displayName': model, 'supportedReasoningEfforts': [],
                       'description': 'Gemini APIで利用します。利用資格・無料枠はAI Studioで確認してください。'}
                      for model in MODELS]
            self._emit('models', models=models, default_model=DEFAULT_MODEL, default_effort=None)
        except ValueError as error:
            self._emit('models_error', message=str(error))
        finally:
            self._emit('models_state', loading=False)
        return True

    def set_approval_reviewer(self, reviewer):
        if reviewer not in ('user', 'auto_review'):
            raise ValueError('未対応の承認方法です。')
        # No tools or permission requests exist in this transport.
        self.approvals_reviewer = reviewer

    def send(self, text, model=None, effort=None):
        self._start(text, model, effort, summary=False)

    def summarize(self, conversation, model=None, effort=None):
        self._start(conversation, model, effort, summary=True)

    def _start(self, text, model, effort, *, summary):
        text = text.strip()
        model = model or DEFAULT_MODEL
        with self._lock:
            if self._closed:
                raise ValueError('この会話は終了しています。')
            if self._busy:
                raise ValueError('実行中です。完了を待つか、中断してください。')
            if self._worker and self._worker.is_alive():
                raise ValueError('前の通信の終了を待っています。少し待って再度送信してください。')
            if not text:
                raise ValueError('お願いを入力してください。')
            if model not in MODELS or effort is not None:
                raise ValueError('対応するGeminiモデルを選んでください。')
            key = self._key_reader(self.credentials_path)
            self._busy = True
            self._cancel = cancel = threading.Event()
            generation = self._generation
            user = {'role': 'user', 'parts': [{'text': text}]}
            contents = [user] if summary else deepcopy(self._history) + [user]
            if not summary:
                self._emit('user', text=text, started_at=time.monotonic())
            self._emit('state', busy=True, message='要約を作成中…' if summary else 'Geminiの応答を待っています…')
            self._worker = threading.Thread(
                target=self._run, args=(generation, cancel, key, model, contents, text, summary),
                name='gemini-chat', daemon=True)
            self._worker.start()

    def _run(self, generation, cancel, key, model, contents, text, summary):
        item_id = uuid.uuid4().hex
        status = 'failed'

        def receive(chunk):
            if summary:
                return
            for candidate in chunk.get('candidates') or []:
                if candidate.get('index', 0) != 0:
                    continue
                parts = (candidate.get('content') or {}).get('parts') or []
                delta = ''.join(part.get('text', '') for part in parts if not part.get('thought'))
                with self._lock:
                    if delta and not self._closed and generation == self._generation and not cancel.is_set():
                        self._emit('delta', generation, item_id=item_id, text=delta)

        try:
            if cancel.is_set():
                return
            instructions = self.summary_instructions if summary else self.instructions
            response = self._transport(model, contents, instructions, key, cancel, receive)
            if cancel.is_set():
                return
            candidates = response.get('candidates') or []
            candidate = candidates[0] if candidates else {}
            content = candidate.get('content') or {}
            parts = content.get('parts') or []
            answer = ''.join(part.get('text', '') for part in parts if not part.get('thought'))
            if not answer or candidate.get('finishReason') not in ('STOP', 'MAX_TOKENS'):
                raise ValueError('Geminiから回答を取得できませんでした。安全性の判定または利用条件をAI Studioで確認してください。')
            with self._lock:
                if self._closed or generation != self._generation or cancel.is_set():
                    return
                if summary:
                    self._emit('summary', generation, text='以下は前の会話からの引き継ぎです。\n\n' + answer)
                else:
                    # Preserve thought signatures in memory for multi-turn API requests.
                    self._history = contents + [{'role': 'model', 'parts': deepcopy(parts)}]
                    self._emit('submitted', generation, text=text)
                    self._emit('assistant', generation, item_id=item_id, text=answer)
                status = 'completed'
                if candidate.get('finishReason') == 'MAX_TOKENS':
                    status = 'incomplete'
                    self._emit('error', generation, message='出力上限に達したため、回答は途中までです。')
        except Exception as error:
            # Never expose raw exceptions: transports may include credentials or request data.
            if not cancel.is_set():
                message = str(error) if isinstance(error, ValueError) and self._transport is generate else 'Geminiの応答に失敗しました。'
                self._emit('error', generation, message=message.replace(key, '[非表示]'))
        finally:
            with self._lock:
                if not self._closed and generation == self._generation and not cancel.is_set():
                    self._busy = False
                    self._worker = None
                    self._emit('state', generation, busy=False, message='入力を待っています。', status=status)

    def interrupt(self):
        with self._lock:
            if not self._busy:
                return
            self._cancel.set()
            self._generation += 1
            self._busy = False
            self._emit('error', message='応答の受信を中断しました。送信済みのGoogle側の処理・利用量を取り消す操作ではありません。')
            self._emit('state', busy=False, message='中断しました。通信終了後に再送信できます。', status='interrupted')

    def reset(self):
        with self._lock:
            if self._busy:
                raise ValueError('実行中のお願いを中断してください。')
            self._generation += 1
            self._history.clear()
            self._emit('reset')

    def answer(self, request_id, result, remember=False):
        return False

    def close(self, wait=True):
        with self._lock:
            self._closed = True
            self._cancel.set()
            self._generation += 1
            self._busy = False
            self._history.clear()
        if wait and self._worker:
            self._worker.join(timeout=31)
