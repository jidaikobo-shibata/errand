"""MCP-originated questions: explicit answers, no automatic URL opening."""
from gi.repository import Gio, Gtk, Pango
from .elicitation import UnsupportedForm, browser_url, confirmation_key, content, fields, options


def text_label(text):
    widget = Gtk.Label(label=str(text), xalign=0, wrap=True, selectable=True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


class ElicitationView(Gtk.Box):
    def __init__(self, params, respond, remember_respond=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.params, self.respond = params, respond
        self.remember_respond = remember_respond
        self.readers = {}
        self.controls = {}
        self.append(text_label('MCPからの確認: ' + str(params.get('serverName', '不明'))))
        self.append(text_label(params.get('message', '確認してください。')))
        self.error = text_label('')
        self.error.set_visible(False)
        self.open_url = lambda url: Gio.AppInfo.launch_default_for_uri(url, None)
        self.mode = params.get('mode', 'form')
        supported = True
        confirmation_only = False
        try:
            if self.mode == 'form':
                self.schema = params.get('requestedSchema')
                properties, required = fields(self.schema)
                confirmation_only = not properties
                body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                for key, spec in properties.items():
                    title = str(spec.get('title') or key) + ('（必須）' if key in required else '')
                    body.append(text_label(title))
                    if spec.get('description'):
                        body.append(text_label(spec['description']))
                    kind = spec['type']
                    choices = options(spec) if kind == 'string' else None
                    default = spec.get('default')
                    if kind == 'boolean' or choices:
                        values = [None, True, False] if kind == 'boolean' else [None] + [v for v, _ in choices]
                        names = ['未選択', 'はい', 'いいえ'] if kind == 'boolean' else ['未選択'] + [n for _, n in choices]
                        widget = Gtk.DropDown.new_from_strings(names)
                        if default is not None and default in values:
                            widget.set_selected(values.index(default))
                        self.readers[key] = lambda w=widget, v=values: v[w.get_selected()]
                    elif kind == 'array':
                        widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                        checks = []
                        for value, title_text in options(spec['items']):
                            check = Gtk.CheckButton(label=title_text)
                            check.set_active(isinstance(default, list) and value in default)
                            widget.append(check)
                            checks.append((value, check))
                        self.readers[key] = lambda pairs=checks: [v for v, w in pairs if w.get_active()]
                    else:
                        widget = Gtk.Entry()
                        if default is not None:
                            widget.set_text(str(default))
                        widget.set_max_length(100000)
                        self.readers[key] = lambda w=widget: w.get_text()
                    widget.update_property([Gtk.AccessibleProperty.LABEL], [title])
                    self.controls[key] = widget
                    body.append(widget)
                scroll = Gtk.ScrolledWindow(max_content_height=220, propagate_natural_height=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
                scroll.set_child(body)
                self.append(scroll)
            elif self.mode == 'url':
                self.url = browser_url(params.get('url'))
                self.append(text_label(self.url))
                self.browser_button = Gtk.Button(label='ブラウザで開く')
                self.browser_button.connect('clicked', self.open_browser)
                self.append(self.browser_button)
            else:
                raise UnsupportedForm('このMCPのフォーム形式にはまだ対応していません。')
        except (ValueError, TypeError, KeyError) as exc:
            supported = False
            self.show_error(str(exc))
        self.append(self.error)
        self.remember = Gtk.CheckButton()
        remember_label = 'このタブでは、同じMCPからの同じ確認を以降許可する'
        self.remember.set_child(Gtk.Label(label=remember_label, wrap=True, xalign=0))
        self.remember.update_property([Gtk.AccessibleProperty.LABEL], [remember_label])
        self.remember.set_visible(supported and confirmation_key(params) is not None and remember_respond is not None)
        self.append(self.remember)
        row = Gtk.Box(spacing=8)
        accept_label = ('許可して続行' if confirmation_only else
                        '回答して続行' if self.mode == 'form' else '確認して続行')
        self.accept_button = Gtk.Button(label=accept_label, sensitive=supported)
        self.accept_button.connect('clicked', self.accept)
        row.append(self.accept_button)
        self.decline_button = Gtk.Button(label='拒否')
        self.decline_button.connect('clicked', lambda _: respond({'action': 'decline', 'content': None}))
        row.append(self.decline_button)
        self.cancel_button = Gtk.Button(label='キャンセル')
        self.cancel_button.connect('clicked', lambda _: respond({'action': 'cancel', 'content': None}))
        row.append(self.cancel_button)
        self.append(row)

    def show_error(self, message):
        self.error.set_text(message)
        self.error.set_visible(True)

    def open_browser(self, _):
        try:
            self.open_url(self.url)
        except Exception:
            self.show_error('ブラウザを開けませんでした。時間をおいて再試行してください。')

    def accept(self, _):
        if not self.accept_button.get_sensitive():
            return
        try:
            value = content(self.schema, {key: read() for key, read in self.readers.items()}) if self.mode == 'form' else None
        except (ValueError, TypeError) as exc:
            self.show_error(str(exc))
            return
        result = {'action': 'accept', 'content': value}
        if self.remember.get_visible() and self.remember.get_active():
            self.remember_respond(result)
        else:
            self.respond(result)
