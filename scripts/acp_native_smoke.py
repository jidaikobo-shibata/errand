#!/usr/bin/env python3
"""Check installed Gemini CLI using loopback-only fake API and temporary files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from errand.gemini_cli import Launcher, file_uri
from chat_foundation.acp.session import Session
with tempfile.TemporaryDirectory(prefix='errand-native-local-') as directory:
    root = Path(directory)
    target = root / 'work-target.txt'
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            calls.append(self.path)
            if len(calls) == 1:
                part = {'functionCall': {'name': 'write_file', 'args': {'file_path': str(target), 'content': 'native-created'}}}
            elif len(calls) == 3:
                part = {'functionCall': {'name': 'run_shell_command', 'args': {'command': 'printf native-shell'}}}
            else:
                part = {'text': '完了しました。'}
            response = {'candidates': [{'content': {'role': 'model', 'parts': [part]}, 'finishReason': 'STOP', 'index': 0}], 'usageMetadata': {'promptTokenCount': 1, 'candidatesTokenCount': 1, 'totalTokenCount': 2}}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: ' + json.dumps(response).encode() + b'\n\n')
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    preload = root / 'loopback-only.cjs'
    preload.write_text('''const net=require('node:net'); const orig=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args){
const o=Array.isArray(args[0])?args[0][0]:args[0];
const host=typeof o==='object'?o.host:(typeof args[1]==='string'?args[1]:'localhost');
if(!['127.0.0.1','localhost','::1'].includes(host)) throw new Error('external network disabled');
return orig.apply(this,args);};''')
    events = []
    def launch(location, instructions, summary=False):
        with patch('errand.gemini_cli.read_api_key', return_value='synthetic-offline-key'):
            spec = Launcher()(location, instructions, summary=summary)
        spec['environment'].update(NODE_OPTIONS='--require=' + str(preload), HOME=directory,
                                   GOOGLE_GEMINI_BASE_URL=f'http://127.0.0.1:{httpd.server_port}')
        return spec
    session = Session(lambda k, d: events.append((k, d)), launch=launch, instructions='offline fixture',
                      summary_instructions='summary', assistant_name='Gemini', required_mode='default',
                      default_model='gemini-3.5-flash-lite', file_uri=file_uri)
    def until(test):
        deadline = time.monotonic() + 20
        while not test():
            if time.monotonic() > deadline:
                raise TimeoutError('local model check timed out')
            time.sleep(.01)
    try:
        target = Path(session._scratch.name) / 'work' / 'native-output.txt'
        session.load_models()
        until(lambda: not session._loading_models)
        assert session._models, [kind for kind, _ in events]
        source = root / '日本語 % file.txt'
        source.write_text('offline fixture')
        session.send('このファイルを確認してください。', files=[str(source)])
        until(lambda: session._requests or not session.busy)
        assert session._requests
        request = next(d for k, d in events if k == 'request')
        assert str(source) in str(request['params'])
        session.answer(request['request_id'], {'cancel': True})
        until(lambda: not session.busy)
        assert not calls
        events.clear()
        print('PASS: native external-file permission, Unicode/space/% and cancellation')
        session.send('テストファイルを作成してください。')
        until(lambda: session._requests or not session.busy)
        assert session._requests, ([d for k, d in events if k in ('progress', 'error')], calls)
        assert not target.exists()
        request = next(d for k, d in events if k == 'request')
        print('PASS: native write tool waits for permission before creating file')
        allow = next(o['optionId'] for o in request['params']['options'] if o['kind'] == 'allow_once')
        session.answer(request['request_id'], {'option_id': allow})
        until(lambda: not session.busy)
        assert target.read_text() == 'native-created'
        print('PASS: native tool creates only the temporary fixture after allow-once')
        events.clear()
        session.send('テストコマンドを実行してください。')
        until(lambda: session._requests or not session.busy)
        assert session._requests
        request = next(d for k, d in events if k == 'request')
        assert 'printf native-shell' in str(request['params'])
        allow = next(o['optionId'] for o in request['params']['options'] if o['kind'] == 'allow_once')
        session.answer(request['request_id'], {'option_id': allow})
        until(lambda: not session.busy)
        assert events[-1][1]['status'] == 'completed'
        print('PASS: native command approval, execution and completion')
        print('Prompt status:', events[-1][1].get('status'), 'Local API calls:', len(calls))
    finally:
        session.close()
        httpd.shutdown()
        httpd.server_close()
