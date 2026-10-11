import json
from pathlib import Path
import tempfile
import unittest
from errand.bookmarks import Bookmarks


class BookmarkTests(unittest.TestCase):
    def test_unrepresentable_characters_are_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bookmarks.json'
            store = Bookmarks(path)
            key = store.save('名前', '依頼\n続き')
            original = path.read_bytes()
            for invalid in ('前\x00後', '\ud800'):
                for name, text in ((invalid, '依頼'), ('名前', invalid)):
                    with self.assertRaises(ValueError):
                        store.save(name, text, key)
                    self.assertEqual(path.read_bytes(), original)
                with self.assertRaises(ValueError):
                    store.save('名前', '依頼', invalid)

    def test_invalid_characters_in_saved_json_are_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bookmarks.json'
            for invalid in ('前\x00後', '\ud800'):
                path.write_text(json.dumps([{'id': 'one', 'name': '名前', 'text': invalid}]))
                original = path.read_bytes()
                store = Bookmarks(path)
                self.assertIsNotNone(store.error)
                self.assertEqual(store.items, [])
                with self.assertRaises(ValueError):
                    store.save('新規', '依頼')
                self.assertEqual(path.read_bytes(), original)

    def test_save_reload_update_delete_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bookmarks.json'
            store = Bookmarks(path)
            key = store.save(' 納品前チェック ', '添付した報告書を確認してください。\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = Bookmarks(path)
            self.assertEqual(loaded.items[0]['name'], '納品前チェック')
            self.assertEqual(loaded.items[0]['text'], '添付した報告書を確認してください。\n')
            loaded.save('改訂', '新しい依頼', key)
            self.assertEqual(len(loaded.items), 1)
            loaded.delete(key)
            self.assertEqual(Bookmarks(path).items, [])

    def test_corrupt_data_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bookmarks.json'
            path.write_text('broken')
            store = Bookmarks(path)
            self.assertIsNotNone(store.error)
            with self.assertRaises(ValueError):
                store.save('名前', '依頼')
            self.assertEqual(path.read_text(), 'broken')

    def test_invalid_edit_leaves_existing_item_unchanged(self):
        store = Bookmarks('/not-written', isolated=True)
        key = store.save('名前', '依頼')
        for name, text in (('', '依頼'), ('名前', '  '), ('x' * 101, '依頼')):
            with self.assertRaises(ValueError):
                store.save(name, text, key)
            self.assertEqual(store.items[0]['text'], '依頼')
        self.assertEqual(store.items[0]['id'], key)
