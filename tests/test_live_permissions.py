from pathlib import Path
import shlex
import unittest

from scripts.live_smoke import Check


class LivePermissionTests(unittest.TestCase):
    def setUp(self):
        self.check = object.__new__(Check)
        self.check.allowed_directory = Path("/tmp/errand-synthetic-check").resolve()

    def test_only_generated_directory_is_permitted(self):
        for path in (str(self.check.allowed_directory), str(self.check.allowed_directory / "merged.pdf")):
            self.assertTrue(self.check.permitted_test_permissions({"fileSystem": {"write": [path]}, "network": None}))
        for path in ("/tmp", "/home", "relative.pdf", str(self.check.allowed_directory / "../other.pdf")):
            self.assertFalse(self.check.permitted_test_permissions({"fileSystem": {"write": [path]}}))

    def test_network_and_broad_entries_are_refused(self):
        self.assertFalse(self.check.permitted_test_permissions({"fileSystem": {"write": [str(self.check.allowed_directory)]},
                                                               "network": {"enabled": True}}))
        self.assertFalse(self.check.permitted_test_permissions({"fileSystem": {"entries": [
            {"path": {"type": "special", "value": {"kind": "root"}}, "access": "write"}]}}))
        self.assertFalse(self.check.permitted_test_permissions({}))

    def test_literal_filesystem_entries(self):
        self.assertTrue(self.check.permitted_test_permissions({"fileSystem": {"entries": [
            {"path": {"type": "path", "path": str(self.check.allowed_directory / "merged.pdf")}, "access": "write"}]}}))

    def test_only_exact_synthetic_merge_command_is_permitted(self):
        root = self.check.allowed_directory
        command = f"qpdf --empty --pages {root}/a.pdf {root}/b.pdf -- {root}/merged.pdf"
        self.assertTrue(self.check.permitted_test_command(command))
        self.assertTrue(self.check.permitted_test_command("/bin/bash -lc " + shlex.quote(command)))
        self.assertFalse(self.check.permitted_test_command(command + "; touch /tmp/extra"))
        self.assertFalse(self.check.permitted_test_command(command.replace("merged.pdf", "a.pdf")))
        self.assertFalse(self.check.permitted_test_command(command.replace(str(root), "/home")))


if __name__ == "__main__":
    unittest.main()
