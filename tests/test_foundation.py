import ast
import importlib
from pathlib import Path
import unittest


class FoundationTests(unittest.TestCase):
    def test_legacy_imports_share_the_implementation(self):
        for name in ('markdown', 'math', 'elicitation'):
            with self.subTest(name=name):
                self.assertIs(importlib.import_module('errand.' + name),
                              importlib.import_module('chat_foundation.core.' + name))

    def test_foundation_has_no_product_imports_and_core_has_no_gtk_imports(self):
        root = Path(__file__).resolve().parents[1] / 'chat_foundation'
        for path in root.rglob('*.py'):
            with self.subTest(path=path):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [node.module or '']
                    else:
                        continue
                    for module in modules:
                        self.assertNotEqual(module.split('.')[0], 'errand')
                        if path.parent.name in ('core', 'codex'):
                            self.assertNotIn(module.split('.')[0], ('gi', 'cairo'))

    def test_errand_connection_adapters_use_shared_implementations(self):
        from chat_foundation.codex.login import Login as SharedLogin
        from chat_foundation.codex.protocol import AppServer, RpcError
        from chat_foundation.codex.session import Session as SharedSession
        from errand.login import Login
        from errand.protocol import AppServer as ErrandServer, RpcError as ErrandError
        from errand.session import Session
        self.assertIs(ErrandServer, AppServer)
        self.assertIs(ErrandError, RpcError)
        self.assertIs(Session.send, SharedSession.send)
        self.assertIs(Login.start, SharedLogin.start)
