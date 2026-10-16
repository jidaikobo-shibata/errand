"""Bounded GTK readiness checks for isolated GUI smoke tests."""
import sys
import time

from gi.repository import GLib


def foreground_on_activate(app):
    """Activate only this test process on macOS; never change OS permissions."""
    if sys.platform != 'darwin':
        return
    import ctypes as C

    C.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = C.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [C.c_char_p]
    objc.objc_getClass.restype = C.c_void_p
    objc.sel_registerName.argtypes = [C.c_char_p]
    objc.sel_registerName.restype = C.c_void_p

    def message(target, name, result=C.c_void_p, types=(), args=()):
        call = C.CFUNCTYPE(result, C.c_void_p, C.c_void_p, *types)(('objc_msgSend', objc))
        return call(target, objc.sel_registerName(name.encode()), *args)

    def activate():
        native = message(objc.objc_getClass(b'NSApplication'), 'sharedApplication')
        message(native, 'setActivationPolicy:', result=C.c_bool, types=(C.c_long,), args=(0,))
        message(native, 'activateIgnoringOtherApps:', result=None, types=(C.c_bool,), args=(True,))
        window = message(native, 'keyWindow') or message(native, 'mainWindow')
        if not window:
            windows = message(native, 'windows')
            for index in range(message(windows, 'count', result=C.c_ulong)):
                candidate = message(windows, 'objectAtIndex:', types=(C.c_ulong,), args=(index,))
                if message(candidate, 'isVisible', result=C.c_bool):
                    window = candidate
                    break
        if window:
            message(window, 'makeKeyAndOrderFront:', result=None, types=(C.c_void_p,), args=(None,))
            message(window, 'makeMainWindow', result=None)
        return False

    app.connect('activate', lambda _: activate())
    app.connect('activate', lambda _: GLib.idle_add(activate))


def input_ready(app):
    window = app.window
    view = window.current
    if not window.props.is_active or not view.input.get_mapped():
        return False
    view.input.grab_focus()
    return (view.input.has_focus() and view.history.get_width() > 0
            and view.scroll.get_vadjustment().get_page_size() > 0)


def wait_for(app, predicate, callback, failures, description, timeout=10):
    """Poll actual readiness and fail explicitly if it never arrives."""
    deadline = time.monotonic() + timeout

    def poll():
        try:
            if not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'GUI readiness timeout: {description}')
                return True
            callback()
        except Exception as error:
            failures.append(error)
            app.quit()
        return False

    return GLib.timeout_add(25, poll)
