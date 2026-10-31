"""Offline ACP agent for lifecycle, permissions and temporary file operations."""
import json
import os
from pathlib import Path
import sys
import threading
from concurrent.futures import Future

lock = threading.Lock()
cancel = threading.Event()
replies = {}
sid = 'offline-session'


def send(message):
    with lock:
        print(json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False), flush=True)


def update(data):
    send({'method': 'session/update', 'params': {'sessionId': sid, 'update': data}})


def prompt(message):
    cancel.clear()
    params = message['params']
    text = params['prompt'][0]['text']
    if text.startswith(('/', '$')):
        (Path.cwd() / 'slash-executed.txt').write_text('unexpected CLI command')
    status = 'end_turn'
    if text == 'CRASH':
        os._exit(3)
    if text in ('WAIT', 'SHORT'):
        cancel.wait(.15 if text == 'SHORT' else 8)
        status = 'cancelled'
    elif text.startswith('WRITE:') or text.startswith('ALWAYS:') or text == 'UNSUPPORTED':
        request_id = message['id']  # Deliberately collides with the client's request ID.
        future = replies[request_id] = Future()
        if text == 'UNSUPPORTED':
            send({'id': request_id, 'method': 'fs/write_text_file', 'params': {
                'sessionId': sid, 'path': str(Path.cwd() / 'unexpected.txt'), 'content': 'must not write'}})
        else:
            target = text.split(':', 1)[1]
            tool = {'sessionUpdate': 'tool_call', 'toolCallId': 'write-tool', 'kind': 'edit',
                    'title': 'Write file', 'status': 'pending',
                    'content': [{'type': 'diff', 'path': target, 'oldText': '', 'newText': 'created'}]}
            update(tool)
            options = [{'optionId': 'forever', 'name': 'Always', 'kind': 'allow_always'}]
            if text.startswith('WRITE:'):
                options += [{'optionId': 'yes', 'name': 'Allow once', 'kind': 'allow_once'},
                            {'optionId': 'no', 'name': 'Deny', 'kind': 'reject_once'}]
            send({'id': request_id, 'method': 'session/request_permission', 'params': {
                'sessionId': sid, 'toolCall': tool, 'options': options}})
        result = future.result(timeout=8)
        outcome = result.get('result', {}).get('outcome', {})
        if outcome.get('optionId') == 'yes':
            Path(target).write_text('created')
        update({'sessionUpdate': 'tool_call_update', 'toolCallId': 'write-tool',
                'status': 'completed' if outcome.get('optionId') == 'yes' else 'failed'})
        status = 'cancelled' if cancel.is_set() else 'end_turn'
    if status == 'end_turn':
        for chunk in ('要約' if text.startswith('次の会話を要約') else '回答', 'です。'):
            update({'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': chunk}})
    send({'id': message['id'], 'result': {'stopReason': status}})


for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if not method:
        future = replies.pop(message['id'], None)
        if future:
            future.set_result(message)
        continue
    if method == 'initialize':
        result = {'protocolVersion': 1, 'agentCapabilities': {}}
    elif method == 'session/new':
        result = {'sessionId': sid, 'modes': {'currentModeId': 'default', 'availableModes': [{'id': m} for m in ('default', 'autoEdit', 'yolo')]},
                  'models': {'currentModelId': 'offline-model', 'availableModels': [
                      {'modelId': 'offline-model', 'name': 'Offline'}, {'modelId': 'auto', 'name': 'Auto'}]}}
        if '--no-models' in sys.argv:
            result.pop('models')
    elif method in ('session/set_model', 'session/set_mode'):
        if method == 'session/set_mode' and '--reject-mode' in sys.argv:
            send({'id': message['id'], 'error': {'code': -32602, 'message': 'mode rejected'}})
            continue
        if method == 'session/set_model' and '--slow-model' in sys.argv:
            continue
        if method == 'session/set_model' and '--no-models' in sys.argv:
            send({'id': message['id'], 'error': {'code': -32601, 'message': 'model selection unsupported'}})
            continue
        if method == 'session/set_mode':
            (Path.cwd() / 'mode.txt').write_text(message['params']['modeId'])
        result = {}
    elif method == 'session/prompt':
        threading.Thread(target=prompt, args=(message,), daemon=True).start()
        continue
    elif method == 'session/cancel':
        cancel.set()
        continue
    else:
        send({'id': message['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
        continue
    send({'id': message['id'], 'result': result})
