"""Named prompts stored locally, separate from UI preferences and chat history."""
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4


def validate(items):
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError('定番のお願いは200件まで保存できます。')
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('保存データの形式が正しくありません。')
        key, name, text = (item.get(field) for field in ('id', 'name', 'text'))
        if (not isinstance(key, str) or not key or key in seen
                or not isinstance(name, str) or not name.strip() or len(name) > 100
                or not isinstance(text, str) or not text.strip() or len(text) > 100000):
            raise ValueError('名前は1〜100文字、依頼文は1〜100000文字で入力してください。')
        seen.add(key)
        result.append({'id': key, 'name': name.strip(), 'text': text})
    return result


class Bookmarks:
    def __init__(self, path, isolated=False):
        self.path = Path(path)
        self.isolated = isolated
        self.items = []
        self.error = None
        if not isolated:
            try:
                self.items = validate(json.loads(self.path.read_text()))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                self.error = f'定番のお願いを読み込めませんでした: {error}'

    def write(self, items):
        checked = validate(items)
        if self.error:
            raise ValueError('読み込めなかった保存データを保護するため、変更できません。')
        if not self.isolated:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix='.bookmarks-', dir=self.path.parent)
            try:
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(checked, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.items = checked

    def save(self, name, text, key=None):
        key = key or str(uuid4())
        item = {'id': key, 'name': name, 'text': text}
        items = [item if old['id'] == key else dict(old) for old in self.items]
        if not any(old['id'] == key for old in self.items):
            items.append(item)
        self.write(items)
        return key

    def delete(self, key):
        self.write([dict(item) for item in self.items if item['id'] != key])
