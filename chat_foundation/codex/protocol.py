"""Bidirectional app-server JSONL transport, independent of products and GTK."""
from concurrent.futures import Future
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


from ..core.process_rpc import JsonRpcProcess, RpcError


class CodexMissing(RpcError):
    pass


def server_command(codex):
    return [
        codex, "app-server", "--stdio",
        "-c", 'approvals_reviewer="user"',
        "-c", 'approval_policy="on-request"',
        "-c", 'sandbox_mode="read-only"',
        "-c", "agents.enabled=false",
    ]


class AppServer(JsonRpcProcess):
    """Compatibility entry point preserving Codex wire messages and defaults."""

    def __init__(self, command, cwd, on_event, on_request, on_disconnect):
        super().__init__(command, cwd, on_event, on_request, on_disconnect, label="Codex")
