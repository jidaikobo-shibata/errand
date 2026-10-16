"""Errand defaults and compatibility entry point for shared Codex login."""
from chat_foundation.codex.login import Login as SharedLogin, validate_login_url
from .product import ERRAND
from .protocol import find_codex


class Login(SharedLogin):
    def __init__(self, emit, dispatch=lambda fn: fn(), codex=None, command=None, client_info=None, resolve_codex=None):
        super().__init__(emit, dispatch=dispatch, codex=codex, command=command,
                         client_info=client_info if client_info is not None else ERRAND.client_info(),
                         resolve_codex=resolve_codex if resolve_codex is not None else find_codex,
                         scratch_prefix="errand-login-")
