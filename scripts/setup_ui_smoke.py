#!/usr/bin/env python3
"""Exercise first-run setup, cancellation, retry and browser login, offline."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib


class FakeSetup:
    active = False
    progress = ('ダウンロード中', .5)

    def __init__(self):
        self.listeners = []

    def subscribe(self, listener):
        self.listeners.append(listener)

    def unsubscribe(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def emit(self, kind, **data):
        for listener in list(self.listeners):
            listener(kind, data)

    def start(self):
        self.active = True
        self.emit('state', active=True)
        self.emit('progress', message='ダウンロード中', fraction=.5)

    def cancel(self):
        self.active = False
        self.emit('cancelled')
        self.emit('state', active=False)

    def close(self):
        self.listeners.clear()


root = Path(__file__).resolve().parent.parent
command = [sys.executable, str(root / 'tests/fake_server.py')]
setup = FakeSetup()
app = Application(isolated=True, command=command + ['--signed-out'], runtime=setup)
stage = 0
deadline = time.monotonic() + 20
failure = []
urls = []


def tick():
    global stage
    try:
        window = app.window.current
        if time.monotonic() > deadline:
            raise RuntimeError(f'Setup UI timeout, stage {stage}')
        if stage == 0:
            if window.models_loading or not window.login_box.get_visible():
                return True
            window.event('models_error', {'setup_required': True, 'message': 'missing'})
            assert window.setup_box.get_visible()
            assert not window.login_box.get_visible()
            assert window.setup_button.get_label() == '動作環境を準備する'
            assert 'ダウンロードしてこのMacに保存します' in window.setup_description.get_text()
            assert 'インターネット接続が必要' not in window.setup_description.get_text()
            assert not window.send_button.get_sensitive()
            window.setup_button.emit('clicked')
            assert not window.setup_button.get_sensitive()
            assert window.setup_progress.get_fraction() == .5
            assert '50%' in window.setup_progress.get_text()
            window.setup_cancel_button.emit('clicked')
            assert window.setup_button.get_sensitive()
            assert '中止しました' in window.status.get_text()
            window.setup_button.emit('clicked')
            setup.emit('error', message='取得できませんでした。もう一度お試しください。')
            setup.active = False
            setup.emit('state', active=False)
            assert window.setup_button.get_sensitive()
            assert not window.setup_progress.get_visible()
            assert '取得できませんでした' in window.status.get_text()
            window.setup_button.emit('clicked')
            setup.active = False
            setup.emit('ready', path='/verified/codex')
            setup.emit('state', active=False)
            assert not window.setup_box.get_visible()
            stage = 1
        elif stage == 1:
            if window.models_loading or not window.login_box.get_visible():
                return True
            assert window.session.codex == '/verified/codex'
            assert window.login.codex == '/verified/codex'
            window.open_login_browser = lambda: urls.append(window.login_url)
            window.session.command = command
            window.login_button.emit('clicked')
            stage = 2
        elif stage == 2:
            if window.models_loading or not window.model_entries:
                return True
            assert urls == ['https://auth.openai.com/oauth/authorize?test=true']
            assert window.send_button.get_sensitive()
            assert not window.setup_box.get_visible()
            assert not window.login_box.get_visible()
            app.quit()
            print('PASS: setup explanation, progress, cancellation, retry and browser login', flush=True)
            return False
        return True
    except Exception as error:
        failure.append(error)
        app.quit()
        return False


app.connect('activate', lambda _: GLib.timeout_add(50, tick))
app.run([sys.argv[0]])
if failure:
    raise failure[0]
