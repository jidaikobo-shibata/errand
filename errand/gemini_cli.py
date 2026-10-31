"""Errand's Gemini CLI discovery and private per-tab runtime composition."""
import json
import os
from pathlib import Path
import shutil
import uuid

from chat_foundation.gemini.credentials import read_api_key


def find_gemini(explicit=''):
    if explicit:
        candidates = [Path(explicit)]
    else:
        found = shutil.which('gemini')
        candidates = ([Path(found)] if found else []) + [
            Path.home() / '.local/bin/gemini', Path('/opt/homebrew/bin/gemini'), Path('/usr/local/bin/gemini')]
        def version(path):
            try:
                return tuple(map(int, path.parent.parent.name.lstrip('v').split('.')))
            except ValueError:
                return ()
        candidates += sorted((Path.home() / '.nvm/versions/node').glob('*/bin/gemini'),
                             key=version, reverse=True)
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise ValueError('Gemini CLIが見つかりません。導入後、環境設定で実行ファイルを指定してください。')


def file_uri(path):
    # Gemini CLI 0.66 strips "file://" without URL-decoding the remaining path.
    # Keep native Unicode, spaces and literal % characters intact in this adapter.
    return 'file://' + str(path)


def approve_attached_read(tool, attached_paths, mode):
    """CLI 0.66 requests this separately from its tool approval mode."""
    if mode not in ('autoEdit', 'yolo') or tool.get('kind') != 'read':
        return False
    call_id = tool.get('toolCallId', '')
    return (isinstance(call_id, str) and call_id.startswith('resolve-prompt-')
            and any(tool.get('title') == 'Allow access to absolute path: ' + path
                    for path in attached_paths))


def is_mode_notice(text, requested_mode):
    # Gemini CLI 0.66 sends this as assistant text while processing set_mode.
    return text == '[MODE_UPDATE] ' + requested_mode


class Launcher:
    def __init__(self, *, executable='', credentials_path=None, mcp_config='', model='gemini-3.5-flash-lite', client_info=None, key_provider=None):
        self.executable, self.credentials_path = executable, credentials_path
        self.mcp_config, self.model = mcp_config, model
        self.client_info = client_info
        self.key_provider = key_provider

    def __call__(self, directory, instructions, *, summary=False):
        binary = find_gemini(self.executable)
        from .secret_store import StoreUnavailable
        try:
            key = self.key_provider() if self.key_provider else None
        except StoreUnavailable:
            # Older installations can continue using their explicitly configured source.
            key = None
        if key is None:
            key = read_api_key(self.credentials_path or Path.home() / '.gemini/.env')
        root = Path(directory)
        work = root / 'work'
        work.mkdir(mode=0o700, exist_ok=True)
        # Stop CLI dotenv discovery at the private workspace, including parent /tmp.
        # --ignore-env alone does not ignore trusted .gemini/.env ancestors.
        (work / '.env').write_text('')
        # A fresh CLI home prevents inherited permanent grants, hooks and persistent history.
        home = root / ('runtime-' + uuid.uuid4().hex)
        config = home / '.gemini'
        config.mkdir(mode=0o700, parents=True)
        settings = {
            'security': {'auth': {'selectedType': 'gemini-api-key'}, 'enablePermanentToolApproval': False},
            'telemetry': {'enabled': False}, 'hooksConfig': {'enabled': False},
            'general': {'enableAutoUpdate': False}, 'mcpServers': {},
            'tools': {'exclude': ['enter_plan_mode', 'exit_plan_mode']},
        }
        if summary:
            settings['tools'] = {'core': []}
        elif self.mcp_config:
            path = Path(self.mcp_config)
            try:
                with path.open() as source:
                    data = source.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise ValueError()
                servers = json.loads(data).get('mcpServers', {})
                if not isinstance(servers, dict) or any(not isinstance(v, dict) for v in servers.values()):
                    raise ValueError()
                settings['mcpServers'] = {name: {**value, 'trust': False} for name, value in servers.items()}
            except (OSError, ValueError, AttributeError):
                raise ValueError('指定したGemini CLIのMCP設定を読み込めませんでした。') from None
        (config / 'settings.json').write_text(json.dumps(settings))
        (home / 'empty.json').write_text('{}')
        (work / 'GEMINI.md').write_text(instructions)
        env = {name: value for name, value in os.environ.items()
               if name in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'HOME', 'TMPDIR',
                           'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                           'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                           'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')}
        env.update({
            'GEMINI_CLI_HOME': str(home),
            'GEMINI_CLI_NO_RELAUNCH': '1', 'GEMINI_TELEMETRY_ENABLED': 'false',
            'GEMINI_CLI_SYSTEM_SETTINGS_PATH': str(home / 'empty.json'),
            'GEMINI_CLI_SYSTEM_DEFAULTS_PATH': str(home / 'empty.json'),
            'GEMINI_CLI_TRUST_WORKSPACE': 'true',
        })
        return {'command': [binary, '--acp', '--approval-mode=default', '--model=' + self.model],
                'cwd': str(work), 'environment': env,
                'client_info': self.client_info or {'name': 'chat-foundation', 'version': '1'},
                'excluded_models': ['auto'],
                'auth': {'methodId': 'gemini-api-key', '_meta': {'api-key': key}}}
