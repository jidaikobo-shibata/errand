from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

from errand.login import Login, validate_login_url
from errand.protocol import AppServer, RpcError


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.finished = threading.Event()
        self.browser = threading.Event()
        self.command = [sys.executable, str(Path(__file__).parent / "fake_server.py")]
        self.login = None

    def emit(self, kind, data):
        self.events.append((kind, data))
        if kind == "state" and not data["active"]:
            self.finished.set()
        if kind == "browser":
            self.browser.set()

    def run_login(self, *flags):
        self.login = Login(self.emit, command=self.command + list(flags))
        self.assertTrue(self.login.start())
        self.assertTrue(self.finished.wait(5), "Login did not finish")

    def tearDown(self):
        if self.login:
            self.login.close()

    def test_browser_login_completion(self):
        self.run_login("--signed-out")
        self.assertTrue(self.browser.is_set())
        self.assertIn("success", [kind for kind, _ in self.events])
        self.assertNotIn("error", [kind for kind, _ in self.events])

    def test_existing_account_is_reused(self):
        self.run_login()
        self.assertFalse(self.browser.is_set())
        self.assertIn("success", [kind for kind, _ in self.events])

    def test_cleanup_failure_releases_busy_state(self):
        original_close = AppServer.close

        def fail_after_close(server):
            original_close(server)
            raise PermissionError("cleanup denied")

        with patch.object(AppServer, "close", fail_after_close):
            self.run_login()
        self.assertFalse(self.login._active)
        self.assertIn(("error", {"message": "cleanup denied"}), self.events)

    def test_provider_without_openai_auth_is_reused(self):
        self.run_login("--no-auth-needed")
        self.assertFalse(self.browser.is_set())
        self.assertIn("success", [kind for kind, _ in self.events])

    def test_untrusted_login_url_is_rejected(self):
        self.run_login("--signed-out", "--unsafe-login")
        self.assertFalse(self.browser.is_set())
        self.assertIn("error", [kind for kind, _ in self.events])

    def test_cancel_stops_listener_without_success(self):
        self.login = Login(self.emit, command=self.command + ["--signed-out", "--wait-login"])
        self.assertTrue(self.login.start())
        self.assertTrue(self.browser.wait(3))
        self.assertFalse(self.login.start())
        self.login.cancel()
        self.assertTrue(self.finished.wait(3))
        self.assertNotIn("success", [kind for kind, _ in self.events])
        self.assertNotIn("error", [kind for kind, _ in self.events])

    def test_login_urls_require_official_https_origin(self):
        self.assertEqual(validate_login_url("https://auth.openai.com/oauth/authorize"),
                         "https://auth.openai.com/oauth/authorize")
        for url in ("http://auth.openai.com/login", "https://auth.openai.com.evil.test/login",
                    "https://attacker@auth.openai.com/login", "https://auth.openai.com:444/login",
                    "file:///tmp/login", "https://example.com/login"):
            with self.subTest(url=url), self.assertRaises((RpcError, ValueError)):
                validate_login_url(url)
