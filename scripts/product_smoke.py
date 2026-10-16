#!/usr/bin/env python3
"""Check product composition without live tools or persistent settings."""
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.product import ERRAND
from errand.ui import Application, GLib

product = replace(ERRAND, application_id='jp.jidaikobo.ProductTest', name='Product Test',
                  client_name='product_test', window_title='Product Test — Conversation', startup_tips=('専用の案内',),
                  instructions='専用の指示', summary_instructions='専用の要約指示',
                  config_directory='product-test', mac_directory='ProductTest', settings_schema=None)
app = Application(product=product, isolated=True, smoke=True)
failures = []


def verify(_):
    try:
        view = app.window.current
        assert app.get_application_id() == product.application_id
        assert app.window.get_title() == product.window_title
        assert view.status.get_text() == product.startup_tips[0]
        assert view.session.client_info == product.client_info()
        assert view.login.client_info == product.client_info()
        assert app.runtime.root == product.runtime_root()
        assert app.runtime.root != ERRAND.runtime_root()
        assert view.session.instructions == product.instructions
        assert view.session.summary_instructions == product.summary_instructions
        assert app.settings.path == product.settings_path()
        assert app.bookmarks.path.parent == product.settings_path().parent
        assert app.settings.gnome is None
        assert app.settings.path != ERRAND.settings_path()
        app.window.new.emit('clicked')
        assert app.window.current.session.instructions == product.instructions
        assert app.window.current.status.get_text() == product.startup_tips[0]
        print('PASS: product identity, instructions, new tabs and separate settings', flush=True)
    except Exception as error:
        failures.append(error)
    finally:
        GLib.idle_add(app.quit)


app.connect('activate', verify)
app.run([sys.argv[0]])
if failures:
    raise failures[0]
