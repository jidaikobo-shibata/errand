"""Compose agent services outside the conversation widgets."""
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


class _NoLogin:
    """API-key sessions have no browser login lifecycle."""

    def close(self):
        pass


class AIServices(CodexServices):
    """Select the backend for new tabs; existing conversations keep their backend."""

    def __init__(self, product, settings):
        super().__init__(product)
        self.settings = settings

    def conversation(self, emit, *, dispatch, codex, command):
        if self.settings.get('ai-provider') == 'gemini-cli':
            from chat_foundation.acp.session import Session as ACPSession
            from .gemini_cli import Launcher, file_uri
            launch = Launcher(executable=self.settings.get('gemini-cli-path'),
                              credentials_path=self.settings.get('gemini-env-path') or None,
                              mcp_config=self.settings.get('gemini-mcp-config'), client_info=self.product.client_info())
            return ACPSession(emit, dispatch=dispatch, launch=launch,
                              instructions=self.product.instructions + self.product.gemini_cli_instructions,
                              summary_instructions=self.product.summary_instructions,
                              assistant_name='Gemini', default_model='gemini-3.5-flash-lite',
                              file_uri=file_uri, required_mode='default',
                              approval_modes=(('default', '自分で承認'), ('autoEdit', '編集を自動承認'),
                                              ('yolo', '全操作を自動承認（YOLO）')), notice=(
                                  'Gemini：ファイル操作・コマンド実行に対応します。承認方法はこのタブだけに適用します。'
                                  'Codexの自動レビュー・OSサンドボックスとは異なります。'))
        if self.settings.get('ai-provider') == 'gemini':
            from chat_foundation.gemini.session import Session as GeminiSession
            return GeminiSession(emit, dispatch=dispatch,
                                 instructions=self.product.instructions + self.product.gemini_instructions,
                                 summary_instructions=self.product.summary_instructions,
                                 credentials_path=self.settings.get('gemini-env-path') or None)
        return super().conversation(emit, dispatch=dispatch, codex=codex, command=command)

    def login(self, emit, *, dispatch, codex, command):
        if self.settings.get('ai-provider') in ('gemini', 'gemini-cli'):
            return _NoLogin()
        return super().login(emit, dispatch=dispatch, codex=codex, command=command)
