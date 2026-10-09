"""Default accelerators and shared shortcut matching for the client."""
import sys

# First binding is shown when restoring defaults; aliases preserve familiar keys.
ACTIONS = {
    'previous-tab': ('左のタブへ', ('<Control>Page_Up', '<Control><Shift>Tab')),
    'next-tab': ('右のタブへ', ('<Control>Page_Down', '<Control>Tab')),
    'new-tab': ('新しいタブ', ('<Control>t',)),
    'close-tab': ('タブを閉じる', ('<Control>w',)),
    'send': ('送信', ('<Control>Return', '<Control>KP_Enter')),
    'previous-message': ('前の発言へ', ('<Control><Alt>Up',)),
    'next-message': ('次の発言へ', ('<Control><Alt>Down',)),
    'first-message': ('最初の発言へ', ('<Control><Alt><Shift>Up',)),
    'latest-message': ('最新へ移動して自動追従', ('<Control><Alt><Shift>Down',)),
    'zoom-in': ('文字を拡大', ('<Control>plus', '<Control>equal', '<Control>KP_Add')),
    'zoom-out': ('文字を縮小', ('<Control>minus', '<Control>KP_Subtract')),
    'zoom-reset': ('文字サイズを100%へ', ('<Control>0', '<Control>KP_0')),
    'preferences': ('環境設定', ('<Control>comma',)),
    'quit': ('Errandを終了', ('<Control>q',)),
    'hide': ('ウィンドウを隠す', ('Escape',)),
}


def bindings(action, overrides, platform=None):
    if action in overrides:
        return (overrides[action],) if overrides[action] else ()
    defaults = ACTIONS[action][1]
    if (platform or sys.platform) == 'darwin':
        if action in ('previous-tab', 'next-tab'):
            return (('<Meta><Alt>Left' if action == 'previous-tab' else '<Meta><Alt>Right'),) + defaults
        if action not in ('hide', 'previous-message', 'next-message', 'first-message', 'latest-message'):
            return tuple(value.replace('<Control>', '<Meta>') for value in defaults) + defaults
    return defaults


def key_signature(key, modifiers):
    from gi.repository import Gdk, Gtk
    modifiers = modifiers & Gtk.accelerator_get_default_mod_mask()
    aliases = {Gdk.KEY_KP_Enter: Gdk.KEY_Return, Gdk.KEY_KP_Add: Gdk.KEY_plus,
               Gdk.KEY_KP_Subtract: Gdk.KEY_minus, Gdk.KEY_KP_0: Gdk.KEY_0,
               Gdk.KEY_ISO_Left_Tab: Gdk.KEY_Tab}
    if key == Gdk.KEY_ISO_Left_Tab:
        modifiers |= Gdk.ModifierType.SHIFT_MASK
    key = Gdk.keyval_to_lower(aliases.get(key, key))
    if key == Gdk.KEY_plus:
        modifiers &= ~Gdk.ModifierType.SHIFT_MASK
    return key, int(modifiers)


def signature(value):
    from gi.repository import Gtk
    valid, key, modifiers = Gtk.accelerator_parse(value)
    if not valid:
        raise ValueError('ショートカットの形式が正しくありません。')
    return key_signature(key, modifiers)


def validate_shortcuts(overrides, opener=''):
    used = {}
    if opener:
        used[signature(opener)] = 'Errandを開く'
    for action, (title, _) in ACTIONS.items():
        for value in bindings(action, overrides):
            sig = signature(value)
            if sig in used and used[sig] != title:
                raise ValueError(f'「{title}」と「{used[sig]}」に同じキーが設定されています。')
            used[sig] = title
