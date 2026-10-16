#!/usr/bin/env python3
"""Copy complete messages, including streaming text and TSV tables, offline."""
from pathlib import Path
import csv
import io
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, GLib

app = Application(isolated=True, smoke=True)
errors = []
def check():
    try:
        view = app.window.current
        copied = []
        view.copy_message_to_clipboard = copied.append
        source = '# 見出し\n\n| 名前 | 値 |\n| --- | --- |\n| **項目** | 12 |\n| `a\\|b` | 34 |\n\n> 引用\n>\n> | A | B |\n> | --- | --- |\n> | x | y |\n\n```python\n  print(1)\n```'
        content = view.add_message('Codex', source, markdown=True)
        button = view.message_widgets[-1].get_child().get_last_child()
        button.emit('clicked')
        assert '名前\t値\n項目\t12\na|b\t34' in copied[-1]
        assert 'A\tB\nx\ty' in copied[-1]
        assert '見出し' in copied[-1] and '引用' in copied[-1] and '  print(1)' in copied[-1]
        assert 'コピーしました' in button.get_tooltip_text()
        content.set_source('| A | B |\n| --- | --- |\n| last | value |')
        button.emit('clicked')
        assert copied[-1] == 'A\tB\nlast\tvalue'
        assert list(csv.reader(io.StringIO(copied[-1]), delimiter='\t')) == [['A', 'B'], ['last', 'value']]
        view.add_message('あなた', '**元の入力**\n二行目')
        user_message = view.message_widgets[-1].get_child()
        assert user_message.get_last_child().get_text() == '**元の入力**\n二行目'
        assert user_message.get_first_child().get_next_sibling() is user_message.get_last_child()
        assert 'last' in view.conversation_text()
        view.event('delta', {'item_id': 'streaming', 'text': '生成途中'})
        streaming = view.messages['streaming']
        assert not streaming.message_copy_button.get_visible()
        view.event('assistant', {'item_id': 'streaming', 'text': '生成完了'})
        assert not streaming.message_copy_button.get_visible()
        view.event('delta', {'item_id': 'final', 'text': '| A | B |\n| --- | --- |\n| x | y |'})
        final = view.messages['final']
        assert not streaming.message_copy_button.get_visible()
        assert not final.message_copy_button.get_visible()
        view.event('assistant', {'item_id': 'final', 'text': '| A | B |\n| --- | --- |\n| x | y |'})
        assert not final.message_copy_button.get_visible()
        view.event('state', {'busy': False, 'message': '入力を待っています。'})
        assert not streaming.message_copy_button.get_visible()
        assert final.message_copy_button.get_visible()
        final.message_copy_button.emit('clicked')
        assert copied[-1] == '生成完了\n\nA\tB\nx\ty'
        view.add_message('あなた', '次のお願い')
        view.event('delta', {'item_id': 'interrupted', 'text': '中断までの内容'})
        partial = view.messages['interrupted']
        assert not partial.message_copy_button.get_visible()
        view.event('state', {'busy': False, 'message': '中断しました。'})
        assert partial.message_copy_button.get_visible()
        assert final.message_copy_button.get_visible()
        partial.message_copy_button.emit('clicked')
        assert copied[-1] == '中断までの内容'
        print('PASS: Codex message copy, TSV tables, nested quotes, code, streaming freshness and no user copy button')
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
    finally:
        app.quit()
    return False
app.connect('activate', lambda _: GLib.idle_add(check))
app.run([])
raise SystemExit(bool(errors))
