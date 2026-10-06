"""Markdown prose and framed, independently copyable fenced code blocks."""
from gi.repository import GObject, GLib, Gtk, Pango

from .markdown import blocks, render


class CodeBlock(Gtk.Frame):
    def __init__(self, block):
        super().__init__()
        self.add_css_class("card")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=12, margin_end=12, margin_top=10, margin_bottom=10)
        self.set_child(body)
        header = Gtk.Box(spacing=8)
        self.language = Gtk.Label(xalign=0, hexpand=True)
        self.language.set_ellipsize(Pango.EllipsizeMode.END)
        self.language.set_max_width_chars(28)
        self.language.add_css_class("dim-label")
        header.append(self.language)
        self.copy_button = Gtk.Button(label="コードをコピー")
        self.copy_button.connect("clicked", self.copy)
        header.append(self.copy_button)
        body.append(header)
        self.code = Gtk.Label(xalign=0, selectable=True, wrap=True)
        self.code.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        attributes = Pango.AttrList()
        attributes.insert(Pango.attr_family_new("monospace"))
        self.code.set_attributes(attributes)
        body.append(self.code)
        self.copy_to_clipboard = lambda text: self.get_clipboard().set(text)
        self.update(block)

    def update(self, block):
        if getattr(self, "block", None) == block:
            return
        self.block = block
        self.language.set_text((block.language or "コード") + (" — 受信中" if not block.complete else ""))
        self.code.set_text(block.text.removesuffix("\n").removesuffix("\r"))
        self.copy_button.set_label("コードをコピー")
        self.copy_button.update_property([Gtk.AccessibleProperty.LABEL],
                                        [f"{block.language or 'コード'}ブロックをコピー"])

    def copy(self, _):
        self.copy_to_clipboard(self.block.text)
        self.copy_button.set_label("コピーしました")


class MarkdownView(Gtk.Box):
    __gsignals__ = {"rendered": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, source=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._source = source
        self._pending = None
        self._parts = []
        self.flush()

    def get_source(self):
        return self._source

    def get_text(self):
        return "\n".join(part.code.get_text() if isinstance(part, CodeBlock) else part.get_text()
                         for part in self._parts)

    def get_label(self):
        return "\n".join(part.code.get_label() if isinstance(part, CodeBlock) else part.get_label()
                         for part in self._parts)

    def set_source(self, source, immediate=False):
        self._source = source
        if immediate:
            if self._pending is not None:
                GLib.source_remove(self._pending)
                self._pending = None
            self.flush()
        elif self._pending is None:
            self._pending = GLib.timeout_add(50, self.flush)

    def flush(self):
        self._pending = None
        parsed = blocks(self._source)
        for index, block in enumerate(parsed):
            if index < len(self._parts) and isinstance(self._parts[index], CodeBlock) != (block.kind == "code"):
                for part in self._parts[index:]:
                    self.remove(part)
                self._parts = self._parts[:index]
            if index == len(self._parts):
                if block.kind == "code":
                    part = CodeBlock(block)
                else:
                    part = Gtk.Label(xalign=0, wrap=True, selectable=True)
                    part.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                self._parts.append(part)
                self.append(part)
            part = self._parts[index]
            if isinstance(part, CodeBlock):
                part.update(block)
            else:
                part.set_markup(render(block.text.rstrip("\r\n")))
        for part in self._parts[len(parsed):]:
            self.remove(part)
        self._parts = self._parts[:len(parsed)]
        self.emit("rendered")
        return False
