#!/usr/bin/env python3
"""Exercise MCP confirmation UI offline on an isolated Wayland display."""
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib
from errand.elicitation_widget import ElicitationView

app = Application(isolated=True, smoke=True)
errors = []

def check():
    try:
        view = app.window.current
        answers = []
        def answer(key, value):
            answers.append((key, value))
            view.remove_request(key)
            return True
        with patch.object(view.session, 'answer', side_effect=answer):
            view.show_request({'request_id': 'form', 'method': 'mcpServer/elicitation/request', 'params': {
                'mode': 'form', 'serverName': 'browser-test', 'message': 'ブラウザで確認しますか？',
                'requestedSchema': {'type': 'object', 'properties': {'allow': {'type': 'boolean'},
                    'width': {'type': 'integer', 'minimum': 320}}, 'required': ['allow', 'width']}}})
            form = view.requests['form'].get_last_child()
            assert isinstance(form, ElicitationView)
            assert form.accept_button.get_label() == '回答して続行'
            form.accept_button.emit('clicked')
            assert not answers and form.error.get_visible()
            form.controls['allow'].set_selected(1)
            form.controls['width'].set_text('390')
            form.accept_button.emit('clicked')
            assert answers[-1] == ('form', {'action': 'accept', 'content': {'allow': True, 'width': 390}})
            assert 'form' not in view.requests
            view.show_request({'request_id': 'url', 'method': 'mcpServer/elicitation/request', 'params': {
                'mode': 'url', 'serverName': 'browser-test', 'message': 'URLで確認してください',
                'url': 'https://example.com/confirm', 'elicitationId': 'flow'}})
            url = view.requests['url'].get_last_child()
            opened = []
            url.open_url = opened.append
            assert not opened
            url.browser_button.emit('clicked')
            assert opened == ['https://example.com/confirm']
            assert 'url' in view.requests
            url.accept_button.emit('clicked')
            assert answers[-1] == ('url', {'action': 'accept', 'content': None})
            for action in ('decline', 'cancel'):
                widget = ElicitationView({'mode': 'form', 'requestedSchema': {'type': 'object', 'properties': {}}},
                                         lambda value: answers.append(value))
                assert widget.accept_button.get_label() == '許可して続行'
                getattr(widget, action + '_button').emit('clicked')
                assert answers[-1] == {'action': action, 'content': None}
            unsupported = ElicitationView({'mode': 'form', 'requestedSchema': {'type': 'object', 'properties': {
                'nested': {'type': 'object'}}}}, answers.append)
            assert not unsupported.accept_button.get_sensitive()
            remembered = []
            remember = ElicitationView({'mode': 'form', 'serverName': 'browser', 'message': 'Allow browser_click?',
                'requestedSchema': {'type': 'object', 'properties': {}}}, answers.append, remembered.append)
            assert remember.remember.get_visible() and not remember.remember.get_active()
            remember.remember.set_active(True)
            remember.accept_button.emit('clicked')
            assert remembered == [{'action': 'accept', 'content': {}}]
            remember.decline_button.emit('clicked')
            assert answers[-1] == {'action': 'decline', 'content': None}
            assert not form.remember.get_visible() and not url.remember.get_visible()
            unsafe = ElicitationView({'mode': 'url', 'url': 'javascript:alert(1)'}, answers.append)
            assert not unsafe.accept_button.get_sensitive()
            assert unsafe.error.get_visible()
            assert not hasattr(unsafe, 'browser_button')
        print('PASS: MCP form validation, typed answers, URL opt-in, decline/cancel and unsupported-schema refusal')
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
    finally:
        app.quit()
    return False

app.connect('activate', lambda _: GLib.idle_add(check))
app.run([])
raise SystemExit(bool(errors))
