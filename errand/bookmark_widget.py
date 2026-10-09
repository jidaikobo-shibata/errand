"""Accessible bookmark menu and local prompt editors, without auto-submission."""
from gi.repository import Adw, GLib, Gtk, Pango


class BookmarkMenu(Gtk.MenuButton):
    def __init__(self, conversation):
        super().__init__(tooltip_text='定番のお願い')
        self.view = conversation
        self.store = conversation.app.bookmarks
        self.update_property([Gtk.AccessibleProperty.LABEL], ['定番のお願い'])
        icon = Gtk.DrawingArea(width_request=16, height_request=16)
        def draw(widget, context, width, height):
            color = widget.get_color()
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            context.set_line_width(1.5)
            context.move_to(4, 2)
            for x, y in ((12, 2), (12, 14), (8, 10), (4, 14)):
                context.line_to(x, y)
            context.close_path()
            context.stroke()
        icon.set_draw_func(draw)
        self.set_child(icon)
        self.menu = Gtk.Popover()
        self.set_popover(self.menu)
        self.menu.connect('notify::visible', lambda *_: self.refresh() if self.menu.get_visible() else None)
        self.refresh()

    def refresh(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        box.append(Gtk.Label(label='定番のお願い', xalign=0))
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if self.store.error or not self.store.items:
            choices.append(Gtk.Label(label=self.store.error or '保存したお願いはありません。', wrap=True,
                                     max_width_chars=32, xalign=0))
        for item in self.store.items:
            button = Gtk.Button()
            name = Gtk.Label(label=item['name'], xalign=0, width_chars=36,
                             max_width_chars=42, wrap=True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            button.set_child(name)
            button.update_property([Gtk.AccessibleProperty.LABEL], [item['name']])
            button.connect('clicked', lambda _, saved=dict(item): self.choose(saved))
            choices.append(button)
        scroll = Gtk.ScrolledWindow(max_content_height=280, propagate_natural_height=True,
                                   hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(choices)
        box.append(scroll)
        save = Gtk.Button(label='今の入力を保存…', sensitive=bool(self.prompt().strip()) and not self.store.error)
        save.connect('clicked', lambda _: self.edit(text=self.prompt()))
        box.append(save)
        manage = Gtk.Button(label='管理…')
        manage.connect('clicked', lambda _: self.manage())
        box.append(manage)
        self.menu.set_child(box)

    def prompt(self):
        buffer = self.view.input.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def choose(self, item):
        self.menu.popdown()
        target = self.view
        if self.prompt() or target.attachments or target.session.busy:
            target = target.owner.new_tab()
        target.input.get_buffer().set_text(item['text'])
        GLib.idle_add(target.focus_input)

    def edit(self, item=None, text='', parent=None, updated=None):
        self.menu.popdown()
        dialog = Adw.Window(title='定番のお願いを編集' if item else '定番のお願いを保存',
                            transient_for=parent or self.view.owner, modal=True,
                            default_width=480, default_height=400)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        name = Adw.EntryRow(title='名前')
        name.set_text(item['name'] if item else '')
        name_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        name_list.add_css_class('boxed-list')
        name_list.append(name)
        body.append(name_list)
        label = Gtk.Label(label='依頼文', xalign=0)
        body.append(label)
        editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        editor.update_property([Gtk.AccessibleProperty.LABEL], ['依頼文'])
        editor.get_buffer().set_text(item['text'] if item else text)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=160)
        scroll.set_child(editor)
        scroll.add_css_class('card')
        body.append(scroll)
        error = Gtk.Label(wrap=True, xalign=0)
        body.append(error)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label='キャンセル')
        cancel.connect('clicked', lambda _: dialog.close())
        buttons.append(cancel)
        save = Gtk.Button(label='保存')
        save.add_css_class('suggested-action')
        def accept(_):
            buffer = editor.get_buffer()
            try:
                self.store.save(name.get_text(), buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True),
                                item['id'] if item else None)
            except (ValueError, OSError) as exc:
                error.set_text(str(exc))
                return
            self.refresh()
            if updated:
                updated()
            dialog.close()
        save.connect('clicked', accept)
        buttons.append(save)
        body.append(buttons)
        dialog.set_content(body)
        dialog.present()
        name.grab_focus()
        return dialog, name, editor, save, error

    def manage(self):
        self.menu.popdown()
        dialog = Adw.Window(title='定番のお願いを管理', transient_for=self.view.owner, modal=True,
                            default_width=480, default_height=400)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class('boxed-list')
        error = Gtk.Label(wrap=True, xalign=0)
        def refresh():
            while (child := rows.get_first_child()) is not None:
                rows.remove(child)
            if not self.store.items:
                rows.append(Gtk.Label(label='保存したお願いはありません。'))
            for item in self.store.items:
                row = Adw.ActionRow()
                row.set_use_markup(False)
                row.set_title(item['name'])
                row.set_title_lines(1)
                edit = Gtk.Button(label='編集', valign=Gtk.Align.CENTER, sensitive=not self.store.error)
                edit.connect('clicked', lambda _, saved=dict(item): self.edit(saved, parent=dialog, updated=refresh))
                row.add_suffix(edit)
                delete = Gtk.Button(label='削除', valign=Gtk.Align.CENTER, sensitive=not self.store.error)
                def confirm(_, saved=dict(item)):
                    alert = Adw.AlertDialog(heading='定番のお願いを削除しますか？', body=saved['name'])
                    alert.add_response('cancel', 'キャンセル')
                    alert.add_response('delete', '削除')
                    alert.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
                    alert.set_default_response('cancel')
                    alert.set_close_response('cancel')
                    def response(_, choice):
                        if choice == 'delete':
                            try:
                                self.store.delete(saved['id'])
                            except (ValueError, OSError) as exc:
                                error.set_text(str(exc))
                                return
                            refresh()
                            self.refresh()
                    alert.connect('response', response)
                    alert.present(dialog)
                delete.connect('clicked', confirm)
                row.add_suffix(delete)
                rows.append(row)
        refresh()
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(rows)
        body.append(scroll)
        error.set_text(self.store.error or '')
        body.append(error)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        new = Gtk.Button(label='追加…', sensitive=not self.store.error)
        new.connect('clicked', lambda _: self.edit(parent=dialog, updated=refresh))
        buttons.append(new)
        close = Gtk.Button(label='閉じる')
        close.connect('clicked', lambda _: dialog.close())
        buttons.append(close)
        body.append(buttons)
        dialog.set_content(body)
        dialog.present()
        return dialog
