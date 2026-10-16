from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SharedCodexTests(unittest.TestCase):
    def test_conversation_and_login_work_without_importing_errand(self):
        # A fresh interpreter also detects indirect product imports during real RPC paths.
        code = r'''
import json
from pathlib import Path
import sys
import threading
import time
from chat_foundation.codex.session import Session
from chat_foundation.codex.login import Login

command = [sys.executable, str(Path('tests/fake_server.py').resolve())]
identity = {'name': 'example', 'title': 'Example', 'version': '1'}
events = []
condition = threading.Condition()
def emit(kind, data):
    with condition:
        events.append((kind, data))
        condition.notify_all()
def wait(predicate):
    deadline = time.monotonic() + 5
    with condition:
        while not predicate():
            remaining = deadline - time.monotonic()
            assert remaining > 0, events
            condition.wait(remaining)
def done():
    wait(lambda: any(kind == 'state' and not data['busy'] for kind, data in events))

session = Session(emit, command=command, client_info=identity,
                  instructions='専用の依頼', summary_instructions='専用の要約')
try:
    session.load_models()
    wait(lambda: any(kind == 'models_state' and not data['loading'] for kind, data in events))
    assert any(kind == 'models' for kind, data in events), events
    events.clear()
    session.send('settings', model='fake-fast', effort='low')
    done()
    response = json.loads(next(data['text'] for kind, data in events if kind == 'assistant'))
    assert response['thread']['developerInstructions'] == '専用の依頼'
    assert response['thread']['ephemeral'] is True
    assert response['turn']['sandboxPolicy'] == {'type': 'readOnly', 'networkAccess': False}
    events.clear()
    session.send('mcp-form', model='fake-fast', effort='low')
    wait(lambda: any(kind == 'request' for kind, data in events))
    request = next(data for kind, data in events if kind == 'request')
    assert session.answer(request['request_id'], {'action': 'decline'})
    done()
    events.clear()
    session.send('wait', model='fake-fast', effort='low')
    wait(lambda: session._turn_id is not None)
    session.interrupt()
    done()
    assert any(data.get('status') == 'interrupted' for kind, data in events if kind == 'state')
    events.clear()
    session.summarize('会話', model='fake-fast', effort='low')
    done()
    summary = next(data['text'] for kind, data in events if kind == 'summary')
    assert json.loads(summary[summary.index('{'):])['developerInstructions'] == '専用の要約'
    server = session._server
finally:
    session.close()
assert server._process.poll() is not None
events.clear()
login = Login(emit, command=command + ['--signed-out'], client_info=identity)
try:
    login.start()
    wait(lambda: any(kind == 'state' and not data['active'] for kind, data in events))
    assert any(kind == 'browser' for kind, data in events), events
    assert any(kind == 'success' for kind, data in events), events
finally:
    login.close()
assert not any(name == 'errand' or name.startswith('errand.') for name in sys.modules)
assert 'gi' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
