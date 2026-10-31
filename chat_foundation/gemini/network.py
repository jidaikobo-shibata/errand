"""Stagger TCP candidates without changing global socket or OS configuration."""
from collections import deque
import errno
import http.client
import os
import selectors
import socket
import time
import urllib.request


CONNECT_DELAY = 0.2
MAX_PENDING = 6


def _interleave(addresses):
    """Keep resolver preference, but alternate address families when possible."""
    queues = {}
    seen = set()
    for address in addresses:
        if address not in seen:
            seen.add(address)
            queues.setdefault(address[0], deque()).append(address)
    ordered = []
    while any(queues.values()):
        for queue in queues.values():
            if queue:
                ordered.append(queue.popleft())
    return ordered


def create_connection(address, timeout=30, source_address=None):
    """Return one connected socket; losing attempts never carry an HTTP request."""
    candidates = deque(_interleave(socket.getaddrinfo(*address, type=socket.SOCK_STREAM)))
    if not candidates:
        raise OSError('接続先が見つかりません。')
    deadline = time.monotonic() + timeout
    next_attempt = time.monotonic()
    pending = {}
    winner = None
    last_error = OSError('接続できませんでした。')
    in_progress = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR}

    with selectors.DefaultSelector() as selector:
        def discard(sock):
            selector.unregister(sock)
            pending.pop(sock)
            sock.close()

        try:
            while candidates or pending:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError('接続が時間切れになりました。')
                if candidates and (not pending or now >= next_attempt):
                    if len(pending) >= MAX_PENDING:
                        discard(next(iter(pending)))
                    family, kind, protocol, _, target = candidates.popleft()
                    sock = None
                    try:
                        sock = socket.socket(family, kind, protocol)
                        sock.setblocking(False)
                        if source_address:
                            sock.bind(source_address)
                        result = sock.connect_ex(target)
                        if result in (0, errno.EISCONN):
                            sock.settimeout(timeout)
                            winner = sock
                            return sock
                        if result not in in_progress:
                            raise OSError(result, os.strerror(result))
                        selector.register(sock, selectors.EVENT_WRITE)
                        pending[sock] = None
                        next_attempt = now + CONNECT_DELAY
                    except OSError as error:
                        last_error = error
                        if sock is not None:
                            sock.close()
                        next_attempt = now
                        continue
                if not pending:
                    continue
                wait = deadline - time.monotonic()
                if candidates:
                    wait = min(wait, max(0, next_attempt - time.monotonic()))
                for ready, _ in selector.select(max(0, wait)):
                    sock = ready.fileobj
                    result = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if result == 0:
                        sock.settimeout(timeout)
                        winner = sock
                        return sock
                    last_error = OSError(result, os.strerror(result))
                    discard(sock)
                    next_attempt = time.monotonic()
            raise last_error
        finally:
            for sock in list(pending):
                selector.unregister(sock)
                if sock is not winner:
                    sock.close()


class HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        def connection(*args, **kwargs):
            result = http.client.HTTPSConnection(*args, **kwargs)
            # Per-instance hook used by HTTPConnection.connect to create its socket.
            # Leave TLS validation, proxy tunnelling and other users of sockets intact.
            result._create_connection = create_connection
            return result

        return self.do_open(connection, request, context=self._context)
