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
                        if path.parent.name == 'core':
                            self.assertNotIn(module.split('.')[0], ('gi', 'cairo'))
