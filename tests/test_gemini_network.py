import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chat_foundation.gemini import network


V6 = (network.socket.AF_INET6, network.socket.SOCK_STREAM, 6, '', ('::1', 443, 0, 0))
V4 = (network.socket.AF_INET, network.socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))


class Socket:
    def __init__(self, result, ready=0):
        self.result, self.ready = result, ready
        self.closed = False
        self.timeout = None

    def setblocking(self, blocking):
        assert not blocking

    def connect_ex(self, address):
        self.address = address
        return self.result

    def getsockopt(self, *args):
        return self.ready

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, address):
        self.source = address

    def close(self):
        self.closed = True


class Selector:
    def __init__(self, clock, ready=None):
        self.clock, self.ready = clock, ready or (lambda sock: False)
        self.pending = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        assert not self.pending

    def register(self, sock, event):
        self.pending[sock] = None

    def unregister(self, sock):
        self.pending.pop(sock)

    def select(self, timeout):
        ready = [(SimpleNamespace(fileobj=sock), 2) for sock in self.pending if self.ready(sock)]
        if not ready:
            self.clock[0] += timeout
        return ready


class NetworkTests(unittest.TestCase):
    def run_connection(self, addresses, sockets, selector, clock, timeout=30, source=None):
        with patch.object(network.socket, 'getaddrinfo', return_value=addresses), \
                patch.object(network.socket, 'socket', side_effect=sockets), \
                patch.object(network.selectors, 'DefaultSelector', return_value=selector), \
                patch.object(network.time, 'monotonic', side_effect=lambda: clock[0]):
            return network.create_connection(('example.invalid', 443), timeout, source)

    def test_slow_first_family_does_not_delay_other_family_until_timeout(self):
        clock = [0]
        stuck = Socket(errno.EINPROGRESS)
        working = Socket(errno.EINPROGRESS)
        selector = Selector(clock, ready=lambda sock: sock is working)
        result = self.run_connection([V6, V6, V4], [stuck, working], selector, clock)
        self.assertIs(result, working)
        self.assertAlmostEqual(clock[0], network.CONNECT_DELAY)
        self.assertTrue(stuck.closed)
        self.assertFalse(working.closed)
        self.assertEqual(working.timeout, 30)

    def test_ipv6_can_win_and_ipv6_only_remains_supported(self):
        for addresses in ([V6, V4], [V6]):
            clock = [0]
            working = Socket(0)
            selector = Selector(clock)
            result = self.run_connection(addresses, [working], selector, clock, source=('::1', 0))
            self.assertIs(result, working)
            self.assertEqual(working.address, V6[-1])
            self.assertEqual(working.source, ('::1', 0))
            self.assertEqual(clock[0], 0)

    def test_definite_failure_advances_without_delay_and_closes_failed_socket(self):
        clock = [0]
        failed, working = Socket(errno.ECONNREFUSED), Socket(0)
        selector = Selector(clock)
        result = self.run_connection([V6, V4], [failed, working], selector, clock)
        self.assertIs(result, working)
        self.assertTrue(failed.closed)
        self.assertEqual(clock[0], 0)

    def test_async_failure_is_closed_and_all_failures_raise(self):
        clock = [0]
        failed = Socket(errno.EINPROGRESS, ready=errno.ECONNREFUSED)
        selector = Selector(clock, ready=lambda sock: True)
        with self.assertRaises(OSError):
            self.run_connection([V6], [failed], selector, clock)
        self.assertTrue(failed.closed)

    def test_total_timeout_is_shared_and_all_pending_sockets_are_closed(self):
        clock = [0]
        sockets = [Socket(errno.EINPROGRESS) for _ in range(2)]
        selector = Selector(clock)
        with self.assertRaises(TimeoutError):
            self.run_connection([V6, V4], sockets, selector, clock, timeout=1)
        self.assertAlmostEqual(clock[0], 1)
        self.assertTrue(all(sock.closed for sock in sockets))

    def test_pending_socket_count_is_bounded(self):
        clock = [0]
        sockets = [Socket(errno.EINPROGRESS) for _ in range(10)]
        addresses = [(V4[0], V4[1], 6, '', ('127.0.0.1', port)) for port in range(10)]
        selector = Selector(clock)
        original = selector.register
        sizes = []

        def register(sock, event):
            original(sock, event)
            sizes.append(len(selector.pending))

        selector.register = register
        with self.assertRaises(TimeoutError):
            self.run_connection(addresses, sockets, selector, clock, timeout=3)
        self.assertLessEqual(max(sizes), network.MAX_PENDING)
        self.assertTrue(all(sock.closed for sock in sockets))

    def test_handler_preserves_tls_context_and_changes_only_its_connection(self):
        context = __import__('ssl').create_default_context()
        handler = network.HTTPSHandler(context=context)
        original = network.socket.create_connection

        def do_open(factory, request, **options):
            connection = factory('example.invalid', timeout=30, **options)
            self.assertIs(connection._create_connection, network.create_connection)
            self.assertIs(connection._context, context)
            self.assertTrue(connection._context.check_hostname)
            self.assertIs(network.socket.create_connection, original)
            return 'response'

        handler.do_open = do_open
        self.assertEqual(handler.https_open(Mock()), 'response')

    def test_resolver_preference_and_family_interleaving(self):
        another_v6 = (*V6[:-1], ('::2', 443, 0, 0))
        self.assertEqual(network._interleave([V6, another_v6, V4, V6]), [V6, V4, another_v6])
        self.assertEqual(network._interleave([V4, V6, another_v6]), [V4, V6, another_v6])
