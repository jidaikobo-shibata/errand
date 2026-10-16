"""Errand executable discovery and compatibility imports for shared transport."""
import os
from pathlib import Path
import shutil
import sys

from chat_foundation.codex.protocol import AppServer, CodexMissing, RpcError, server_command


def find_codex(explicit=None, *, runtime_root=None, missing_message="Errandの動作環境を準備してください。"):
    """Desktop launchers do not normally inherit the terminal's PATH."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise RpcError("Codexの実行ファイルを絶対パスで指定してください。")
        return str(path)
    from .runtime import managed_codex
    prepared = managed_codex(root=runtime_root)
    if prepared:
        return prepared
    found = shutil.which("codex")
    if found:
        return found
    candidates = [Path.home() / '.local/bin/codex', Path('/opt/homebrew/bin/codex'),
                  Path('/usr/local/bin/codex')]
    if sys.platform == 'darwin':
        for parent in (Path('/Applications'), Path.home() / 'Applications'):
            candidates.append(parent / 'Codex.app/Contents/Resources/codex')
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    candidates = list((Path.home() / ".nvm/versions/node").glob("*/bin/codex"))

    def version(path):
        try:
            return tuple(int(part) for part in path.parent.parent.name.lstrip("v").split("."))
        except ValueError:
            return ()

    for candidate in sorted(candidates, key=version, reverse=True):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise CodexMissing(missing_message)
