"""Read an explicit environment or dotenv source without executing its contents."""
import os
from pathlib import Path
import re


def read_api_key(path=None, environ=None):
    environ = os.environ if environ is None else environ
    key = environ.get('GEMINI_API_KEY', '').strip()
    if not key and path is not None:
        try:
            with Path(path).open() as source:
                data = source.read(65537)
                if len(data) > 65536:
                    raise ValueError('Gemini APIキー設定ファイルが大きすぎます。')
                for line in data.splitlines():
                    match = re.fullmatch(r'\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.*?)\s*', line)
                    if match:
                        key = match[1]
                        if key[:1] in ('"', "'"):
                            if len(key) < 2 or key[-1] != key[0]:
                                raise ValueError('Gemini APIキー設定の引用符を確認してください。')
                            key = key[1:-1]
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            raise ValueError('Gemini APIキー設定を読み込めませんでした。') from None
    if not key:
        raise ValueError('GEMINI_API_KEYがありません。環境変数または指定したキー設定ファイルを確認してください。')
    # Keys are opaque credentials, not a fixed alphabet or prefix. In particular,
    # newer authorization keys contain dots. Reject whitespace/control/non-ASCII
    # characters for header safety; let Google validate the credential itself.
    if len(key) > 16384 or not re.fullmatch(r'[!-~]+', key):
        raise ValueError('Gemini APIキーの形式を確認してください。')
    return key
