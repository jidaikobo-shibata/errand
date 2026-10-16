"""The conversation operations used by the shared view.

Implementations deliver events through the dispatch function provided by the host.
Events retain their existing names and payloads during the incremental migration.
This protocol describes the existing behavior; it adds no runtime wrapper.
"""
from typing import Protocol


class ConversationController(Protocol):
    codex: str | None
    approvals_reviewer: str

    @property
    def busy(self) -> bool: ...

    def load_models(self): ...

    def set_approval_reviewer(self, reviewer): ...

    def send(self, text, model=None, effort=None): ...

    def interrupt(self): ...

    def reset(self): ...

    def answer(self, request_id, result, remember=False): ...

    def summarize(self, conversation, model=None, effort=None): ...

    def close(self, wait=True): ...
