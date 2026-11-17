#!/usr/bin/env python3
"""Check the application icon with an isolated GTK session."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.ui import Application, Gdk, GLib, Gtk

app = Application(smoke=True, isolated=True)
errors = []


def check(_):
    try:
        name = app.product.application_id
        assert app.window.get_icon_name() == name
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        assert theme.has_icon(name)
        icon = theme.lookup_icon(name, None, 64, 1, Gtk.TextDirection.LTR, 0)
        assert icon.get_file().get_path().endswith(name + '.png')
        print('PASS: application icon name and bundled image lookup')
    except Exception as error:
        errors.append(error)
    finally:
        GLib.idle_add(app.quit)


app.connect('activate', check)
app.run([])
if errors:
    raise errors[0]
