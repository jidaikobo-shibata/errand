#!/usr/bin/env python3
"""Run inside a test .app and reopen it via Launch Services, without live Codex."""
import argparse
import ctypes as C
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if not getattr(sys, 'frozen', False):
    root = next(parent for parent in Path(__file__).resolve().parents
                if (parent / 'errand/ui.py').is_file())
    sys.path.insert(0, str(root))
from errand.product import ERRAND
from errand.ui import Application, Gdk, GLib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--result', type=Path, required=True)
parser.add_argument('--fake-server', type=Path, required=True)
parser.add_argument('--without-handler', action='store_true', help='Reproduce the original failure')
parser.add_argument('--self-event', action='store_true', help='Send reopen to this PID for diagnosis')
args = parser.parse_args()
product = replace(ERRAND, application_id='jp.jidaikobo.Errand.ReopenTest', name='Errand Reopen Test')
app = Application(product=product, isolated=True,
                  command=['/usr/bin/python3', str(args.fake_server)])
deadline = time.monotonic() + 20
phase = 'prepare'
cycle = 0
activations = 0
opener = None
checks = []
diagnostics = []
snapshot = None
wait_until = 0


def activated(_):
    global activations
    activations += 1


def finish(error=None):
    args.result.write_text(json.dumps({
        'passed': error is None, 'checks': checks, 'activations': activations,
        'error': str(error) if error else None,
        'diagnostics': diagnostics,
    }, ensure_ascii=False, indent=2) + '\n')
    app.quit()
    return False


def state():
    return (tuple(app.window.conversations), app.window.tabs.get_selected_page(),
            tuple((view.input.get_buffer().props.text, view.conversation_text())
                  for view in app.window.conversations))


def tick():
    global phase, cycle, opener, snapshot, wait_until
    try:
        if time.monotonic() > deadline:
            raise TimeoutError(f'Reopen did not complete: {phase}, cycle {cycle}')
        window = app.window
        if window is None or time.monotonic() < wait_until:
            return True
        if phase == 'prepare':
            if not window.get_mapped():
                return True
            assert app.mac_reopen is not None, 'Native reopen handler was not installed'
            if args.without_handler:
                app.mac_reopen.close()
                app.mac_reopen = None
            window.current.add_message('あなた', '非表示中も保持する会話')
            window.new_tab()
            window.current.input.get_buffer().set_text('再表示テストの下書き')
            snapshot = state()
            phase = 'hide'
        elif phase == 'hide':
            if cycle == 1:
                window.close()  # Exercise the real close-request handler.
            else:
                assert window.key_controller.emit('key-pressed', Gdk.KEY_Escape, 0,
                                                  Gdk.ModifierType(0))
            assert not window.get_visible()
            phase = 'open'
            wait_until = time.monotonic() + .2
        elif phase == 'open':
            assert not window.get_visible(), 'Hidden window reappeared without a reopen event'
            if app.mac_reopen:
                handler, refcon = C.c_void_p(), C.c_void_p()
                status = app.mac_reopen.lib.AEGetEventHandler(
                    int.from_bytes(b'aevt', 'big'), int.from_bytes(b'rapp', 'big'),
                    C.byref(handler), C.byref(refcon), False,
                )
                diagnostics.append({'handler_status': status,
                                    'handler_owned': handler.value == C.cast(app.mac_reopen.callback, C.c_void_p).value})
            if args.self_event:
                send_self_event()
                opener = subprocess.Popen(['/usr/bin/true'])
            else:
                opener = subprocess.Popen(['/usr/bin/open', '-a', str(args.bundle)])
            phase = 'visible'
        elif phase == 'visible':
            if opener.poll() is None:
                return True
            assert opener.returncode == 0, f'Launch Services failed: {opener.returncode}'
            if not window.get_visible() or not window.get_mapped():
                return True
            assert state() == snapshot, 'Reopen replaced tabs, conversation or draft'
            assert activations == cycle + 2, 'Reopen did not activate the existing application'
            checks.append('close-button' if cycle == 1 else 'Escape')
            cycle += 1
            if cycle == 3:
                return finish()
            phase = 'hide'
            wait_until = time.monotonic() + .2
        return True
    except Exception as error:
        return finish(error)


def send_self_event():
    class Descriptor(C.Structure):
        _layout_ = 'ms'
        _pack_ = 2
        _fields_ = [('kind', C.c_uint32), ('data', C.c_void_p)]

    lib = C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
    desc = C.POINTER(Descriptor)
    lib.AECreateDesc.argtypes = [C.c_uint32, C.c_void_p, C.c_long, desc]
    lib.AECreateAppleEvent.argtypes = [C.c_uint32, C.c_uint32, desc, C.c_int16, C.c_int32, desc]
    lib.AESendMessage.argtypes = [desc, desc, C.c_int32, C.c_long]
    lib.AEDisposeDesc.argtypes = [desc]
    target, event, reply = Descriptor(), Descriptor(), Descriptor()
    pid = C.c_int32(os.getpid())
    try:
        assert lib.AECreateDesc(int.from_bytes(b'kpid', 'big'), C.byref(pid), C.sizeof(pid), C.byref(target)) == 0
        assert lib.AECreateAppleEvent(int.from_bytes(b'aevt', 'big'), int.from_bytes(b'rapp', 'big'),
                                      C.byref(target), -1, 0, C.byref(event)) == 0
        assert lib.AESendMessage(C.byref(event), C.byref(reply), 1, -1) == 0
    finally:
        for value in (reply, event, target):
            lib.AEDisposeDesc(C.byref(value))


app.connect('activate', activated)
GLib.timeout_add(25, tick)
app.run([sys.argv[0]])
