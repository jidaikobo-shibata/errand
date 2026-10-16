"""Initialize an app-server connection without product or GTK dependencies."""
from concurrent.futures import CancelledError
import time


def initialize(server, client_info, timeout=30, cancel=None):
    pending = server.request("initialize", {
        "clientInfo": dict(client_info),
        "capabilities": {"experimentalApi": False},
    })
    if cancel is None:
        pending.result(timeout=timeout)
    else:
        deadline = time.monotonic() + timeout
        while True:
            if cancel.is_set():
                raise CancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            try:
                pending.result(timeout=min(.2, remaining))
                break
            except TimeoutError:
                if pending.done():
                    raise
    server.notify("initialized")
