"""The conversation operations used by the shared view.

Implementations deliver events through the dispatch function provided by the host.
Events retain their existing names and payloads during the incremental migration.
This protocol describes the existing behavior; it adds no runtime wrapper.

Controllers may supply assistant_name, notice, supports_files, supports_approvals
and supports_reasoning. The shared view defaults to the existing Codex behavior
when those optional display/capability attributes are absent.
supports_auto_review=False keeps the approval selector at manual review without
changing the saved Codex preference. supports_file_references=True opts into the
optional send(files=[absolute_paths]) argument, leaving legacy send calls intact.
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
