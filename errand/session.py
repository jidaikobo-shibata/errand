"""Errand defaults and compatibility entry point for the shared controller."""
from chat_foundation.codex.session import Session as SharedSession, LoginRequired, LOGIN_GUIDANCE
from .product import ERRAND, INSTRUCTIONS, SUMMARY_INSTRUCTIONS
from .protocol import find_codex


class Session(SharedSession):
    def __init__(self, emit, dispatch=lambda fn: fn(), codex=None, command=None, instructions=INSTRUCTIONS,
                 summary_instructions=SUMMARY_INSTRUCTIONS, client_info=None, resolve_codex=None):
        super().__init__(emit, dispatch=dispatch, codex=codex, command=command,
                         instructions=instructions, summary_instructions=summary_instructions,
                         client_info=client_info if client_info is not None else ERRAND.client_info(),
                         resolve_codex=resolve_codex if resolve_codex is not None else find_codex,
                         scratch_prefix="errand-")

    @staticmethod
    def _initialize(server, timeout=30, cancel=None, client_info=None):
        SharedSession._initialize(server, timeout=timeout, cancel=cancel,
                                  client_info=client_info if client_info is not None else ERRAND.client_info())
