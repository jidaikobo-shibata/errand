#!/usr/bin/env python3
"""Check native URI conversion, GDK deserialization and attachment widgets."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gdk, Gio, GLib, Gtk


def gtk422_uri(path):
    """Reproduce the old macOS backend with the real Foundation APIs."""
    import ctypes
    ctypes.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p

    def message(target, name, result=ctypes.c_void_p, types=(), args=()):
        call = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p, *types)(('objc_msgSend', objc))
        return call(target, objc.sel_registerName(name.encode()), *args)

    pool = message(message(objc.objc_getClass(b'NSAutoreleasePool'), 'alloc'), 'init')
    try:
        value = message(objc.objc_getClass(b'NSString'), 'stringWithUTF8String:',
                        types=(ctypes.c_char_p,), args=(('file://' + str(path)).encode(),))
        allowed = message(objc.objc_getClass(b'NSCharacterSet'), 'URLPathAllowedCharacterSet')
        value = message(value, 'stringByAddingPercentEncodingWithAllowedCharacters:',
                        types=(ctypes.c_void_p,), args=(allowed,))
        return message(value, 'UTF8String', result=ctypes.c_char_p).decode()
    finally:
        message(pool, 'drain', result=None)


def deserialize(uris):
    loop = GLib.MainLoop()
    values, errors = [], []

    def done(stream, result, _):
        try:
            success, files = Gdk.content_deserialize_finish(result)
            assert success
            values.append(files)
        except Exception as error:
            errors.append(error)
        finally:
            loop.quit()

    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(('\r\n'.join(uris) + '\r\n').encode()))
    Gdk.content_deserialize_async(stream, 'text/uri-list', Gdk.FileList.__gtype__,
                                  GLib.PRIORITY_DEFAULT, None, done, None)
    timer = GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
    try:
        loop.run()
    finally:
        if GLib.MainContext.default().find_source_by_id(timer):
            GLib.source_remove(timer)
    if errors:
        raise errors[0]
    assert len(values) == 1, 'File URI deserialization timed out'
    return values[0]


root = Path(__file__).resolve().parent.parent
app = Application(isolated=True, command=[sys.executable, str(root / 'tests/fake_server.py')])
failures = []


def run(_):
    try:
        view = app.window.current
        with tempfile.TemporaryDirectory(prefix='errand-native-drop-') as directory:
            files = [Path(directory) / name for name in
                     ('example.txt', '報告書 one.txt', 'a:b#c%.txt', 'literal%20.txt')]
            for file in files:
                file.write_text('検証用ファイル\n')
            normal = [file.as_uri() for file in files]
            assert view.file_drop.emit('drop', deserialize(normal), 0., 0.)
            assert list(view.attachments) == [str(file) for file in files]
            assert view.attachment_scroll.get_visible()
            view.clear_attachments()
            if sys.platform == 'darwin':
                broken = [gtk422_uri(file) for file in files]
                native = deserialize(broken)
                assert all(file.get_path() is None for file in native.get_files())
                assert view.file_drop.emit('drop', native, 0., 0.)
                assert list(view.attachments) == [str(file) for file in files]
                assert view.file_drop.emit('drop', native, 0., 0.)
                assert len(view.attachments) == len(files)
                assert [row.get_first_child().get_label() for row in view.attachments.values()] == [file.name for file in files]
            for uri in ('https://example.com/report.txt', 'file%3A//server/share/report.txt',
                        Path(directory).as_uri(), (Path(directory) / 'missing.txt').as_uri()):
                before = list(view.attachments)
                assert not view.file_drop.emit('drop', deserialize([normal[0], uri]), 0., 0.)
                assert list(view.attachments) == before  # No partial acceptance.
            assert view.session.thread_id is None  # A drop never submits a prompt.
            view.clear_attachments()
            assert not view.attachments and not view.attachment_scroll.get_visible()
            print('PASS: native file URI conversion, Japanese/space/symbol names, duplicates and invalid drops', flush=True)
    except Exception as error:
        import traceback
        traceback.print_exc()
        failures.append(error)
    finally:
        app.quit()


app.connect('activate', run)
raise SystemExit(1 if app.run([sys.argv[0]]) or failures else 0)
