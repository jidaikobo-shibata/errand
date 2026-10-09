from pathlib import Path
import tempfile
import unittest
from errand.bookmarks import Bookmarks


class BookmarkTests(unittest.TestCase):
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
