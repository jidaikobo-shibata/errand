"""Compose Codex services outside the conversation widgets."""
from functools import partial

from .protocol import find_codex
from .login import Login
from .runtime import RuntimeSetup
from .session import Session


class CodexServices:
    def __init__(self, product):
        self.product = product
        self.resolve_codex = partial(find_codex, runtime_root=product.runtime_root(),
                                     missing_message=f"{product.name}の動作環境を準備してください。")

    def conversation(self, emit, *, dispatch, codex, command):
        return Session(emit, dispatch=dispatch, codex=codex, command=command,
                       instructions=self.product.instructions,
                       summary_instructions=self.product.summary_instructions,
                       client_info=self.product.client_info(), resolve_codex=self.resolve_codex)

    def login(self, emit, *, dispatch, codex, command):
        return Login(emit, dispatch=dispatch, codex=codex, command=command,
                     client_info=self.product.client_info(), resolve_codex=self.resolve_codex)

    def runtime(self, *, dispatch):
        return RuntimeSetup(dispatch=dispatch, root=self.product.runtime_root(),
                            client_info=self.product.client_info(), resolve_codex=self.resolve_codex)
