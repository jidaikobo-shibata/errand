"""GTK math display using Pango/Cairo, with source copy and text fallback."""
from dataclasses import dataclass
import cairo
from gi.repository import Gtk, Pango, PangoCairo

from .math import parse_formula


@dataclass
class Layout:
    width: float
    height: float
    baseline: float
    texts: list
    lines: list

    def moved(self, x, y):
        return Layout(self.width, self.height, self.baseline,
                      [(layout, tx + x, ty + y) for layout, tx, ty in self.texts],
                      [(a + x, b + y, c + x, d + y) for a, b, c, d in self.lines])


def layout_formula(formula, context, size):
    if formula.kind == "text":
        text = PangoCairo.create_layout(context)
        font = Pango.FontDescription("sans")
        font.set_absolute_size(size * Pango.SCALE)
        text.set_font_description(font)
        text.set_text(formula.text, -1)
        width, height = text.get_pixel_size()
        padding = 3 if formula.text in {"+", "-", "=", "×", "÷", "±", "∓", "≤", "≥", "≠"} else 0
        return Layout(width + padding * 2, height, text.get_baseline() / Pango.SCALE,
                      [(text, padding, 0)], [])
    script = formula.kind in {"sup", "sub"}
    fraction = formula.kind == "fraction"
    children = [layout_formula(child, context, size * (.72 if script and i == 1 else
                                                     .88 if fraction else 1))
                for i, child in enumerate(formula.children)]
    if fraction:
        top, bottom = children
        width = max(top.width, bottom.width) + 10
        line = top.height + 3
        placed = [top.moved((width - top.width) / 2, 0),
                  bottom.moved((width - bottom.width) / 2, line + 4)]
        height, baseline = line + 4 + bottom.height, line + size * .25
        lines = [(1, line, width - 1, line)]
    elif script:
        base, power = children
        if formula.kind == "sup":
            py = 0
            by = max(0, power.height - base.baseline * .45)
        else:
            by = 0
            py = base.baseline - power.baseline * .2
        placed = [base.moved(0, by), power.moved(base.width + 1, py)]
        width = base.width + 1 + power.width
        height = max(by + base.height, py + power.height)
        baseline, lines = by + base.baseline, []
    elif formula.kind in {"root", "box"}:
        child = children[0]
        if formula.kind == "root":
            pad = size * .75
            width, height, baseline = child.width + pad + 3, child.height + 4, child.baseline + 4
            placed = [child.moved(pad, 4)]
            lines = [(1, height * .55, pad * .25, height * .5),
                     (pad * .25, height * .5, pad * .45, height - 2),
                     (pad * .45, height - 2, pad - 1, 1), (pad - 1, 1, width, 1)]
        else:
            width, height, baseline = child.width + 12, child.height + 8, child.baseline + 4
            placed = [child.moved(6, 4)]
            lines = [(1, 1, width - 1, 1), (1, height - 1, width - 1, height - 1),
                     (1, 1, 1, height - 1), (width - 1, 1, width - 1, height - 1)]
    else:
        baseline = max((child.baseline for child in children), default=size)
        height = max((baseline + child.height - child.baseline for child in children), default=size)
        placed, width, lines = [], 0, []
        for child in children:
            placed.append(child.moved(width, baseline - child.baseline))
            width += child.width
    return Layout(width, height, baseline,
                  [text for child in placed for text in child.texts],
                  lines + [line for child in placed for line in child.lines])


class MathBlock(Gtk.Box):
    def __init__(self, block):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.canvas = Gtk.DrawingArea(halign=Gtk.Align.START, accessible_role=Gtk.AccessibleRole.IMG)
        self.canvas.set_draw_func(self.draw)
        scroll = Gtk.ScrolledWindow(max_content_height=600, propagate_natural_height=True)
        scroll.set_child(self.canvas)
        self.append(scroll)
        self.scroll = scroll
        self.fallback = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.fallback.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.append(self.fallback)
        self.copy_button = Gtk.Button(label="式をコピー", halign=Gtk.Align.START)
        self.copy_button.connect("clicked", self.copy)
        self.append(self.copy_button)
        self.copy_to_clipboard = lambda text: self.get_clipboard().set(text)
        self.layout = None
        self.update(block)

    def update(self, block):
        if getattr(self, "block", None) == block:
            return
        self.block = block
        self.layout = None
        self.copy_button.set_label("式をコピー")
        self.copy_button.update_property([Gtk.AccessibleProperty.LABEL], ["数式のLaTeX記法をコピー"])
        self.copy_button.set_tooltip_text("LaTeX記法をコピーします")
        self.fallback.set_text(block.text)
        self.fallback.set_tooltip_text("受信中の数式" if not block.complete else "描画できない数式の元の記法")
        if block.complete:
            try:
                formula = parse_formula(block.text)
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                self.layout = layout_formula(formula, cairo.Context(surface), 18)
                if self.layout.width > 2048 or self.layout.height > 1024:
                    raise ValueError("数式の表示が大きすぎます。")
                readable = "数式: " + formula.readable()
                self.canvas.update_property([Gtk.AccessibleProperty.LABEL], [readable])
                self.canvas.set_tooltip_text(readable)
                self.canvas.set_content_width(int(self.layout.width) + 8)
                self.canvas.set_content_height(int(self.layout.height) + 8)
                self.canvas.queue_draw()
            except (ValueError, RecursionError):
                self.layout = None
        self.scroll.set_visible(self.layout is not None)
        self.fallback.set_visible(self.layout is None)

    def draw(self, widget, context, width, height):
        if self.layout is None:
            return
        color = widget.get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        context.translate(4, 4)
        for layout, x, y in self.layout.texts:
            context.move_to(x, y)
            PangoCairo.show_layout(context, layout)
        context.set_line_width(1.2)
        for a, b, c, d in self.layout.lines:
            context.move_to(a, b)
            context.line_to(c, d)
        context.stroke()

    def copy(self, _):
        self.copy_to_clipboard(self.block.text)
        self.copy_button.set_label("コピーしました")

    def get_text(self):
        return self.block.text

    def get_label(self):
        return self.block.text
