"""Regressions for isolated process cleanup and failed connections."""
from concurrent.futures import Future
from pathlib import Path
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from errand.protocol import AppServer
from errand.session import Session


class ShutdownTests(unittest.TestCase):
    def test_signal_resistant_child_is_stopped_with_parent(self):
        with tempfile.TemporaryDirectory(prefix="errand-test-") as directory:
            ready = Path(directory) / "child.pid"
            code = """import os,signal,sys,time
pid=os.fork()
if pid==0:
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 open(sys.argv[1],'w').write(str(os.getpid()))
 time.sleep(60)
 os._exit(0)
while not os.path.exists(sys.argv[1]): time.sleep(.01)
sys.stdin.read()
"""
            server = AppServer([sys.executable, "-c", code, str(ready)], directory,
                               lambda _: None, lambda _: None, lambda _: None)
            child = None
            try:
                server.start()
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                child = int(ready.read_text())
                # Leader exits first, retaining a signal-resistant child and pipes.
                server._process.stdin.close()
                server._process.wait(timeout=2)
                server.close()
                stat = Path(f"/proc/{child}/stat")
                self.assertTrue(not stat.exists() or stat.read_text().split(") ", 1)[1].startswith("Z"))
                self.assertFalse(any(reader.is_alive() for reader in server._readers))
            finally:
                server.close()
                if child:
                    try:
                        os.kill(child, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_async_close_returns_before_cleanup_completes(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        class SlowServer:
            def close(self):
                entered.set()
                release.wait(3)
                finished.set()
        session = Session(lambda *_: None)
        session._server = SlowServer()
        try:
            session.close(wait=False)
            self.assertTrue(session._closed)
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            self.assertTrue(finished.wait(1))

    def test_failed_initialize_is_disposed_and_retry_starts_new_thread(self):
        servers, events = [], []
        class Server:
            def __init__(self, *args):
                self.calls, self.closed = [], False
                servers.append(self)
            def start(self):
                pass
            def notify(self, *args):
                pass
            def close(self):
                self.closed = True
            def request(self, method, params=None):
                self.calls.append((method, params))
                future = Future()
                if method == "initialize" and len(servers) == 1:
                    future.set_exception(RuntimeError("initialize failed"))
                elif method == "thread/start":
                    future.set_result({"thread": {"id": "new-thread"}, "model": "test"})
                elif method == "turn/start":
                    future.set_result({"turn": {"id": "turn", "status": "completed"}})
                else:
                    future.set_result({})
                return future
        with patch("errand.session.AppServer", Server):
            session = Session(lambda kind, data: events.append((kind, data)), command=["fake"])
            try:
                session.send("first")
                deadline = time.monotonic() + 3
                while session.busy and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(servers[0].closed)
                self.assertIsNone(session._server)
                session.send("retry")
                while session.busy and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(len(servers), 2)
                self.assertEqual([method for method, _ in servers[1].calls],
                                 ["initialize", "thread/start", "turn/start"])
                self.assertEqual(servers[1].calls[-1][1]["threadId"], "new-thread")
            finally:
                session.close()
