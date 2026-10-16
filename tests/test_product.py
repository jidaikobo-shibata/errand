from dataclasses import replace
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from errand.product import ERRAND
from errand.settings import Settings


class ProductTests(unittest.TestCase):
    def test_existing_errand_locations_on_linux_and_mac(self):
        with patch('errand.product.sys.platform', 'linux'):
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/product-config'}):
                self.assertEqual(ERRAND.settings_path(), Path('/tmp/product-config/errand/settings.json'))
                self.assertEqual(Settings(isolated=True).path, ERRAND.settings_path())
        with patch('errand.product.sys.platform', 'darwin'):
            self.assertEqual(ERRAND.settings_path(),
                             Path.home() / 'Library/Application Support/Errand/settings.json')

    def test_product_settings_do_not_share_saved_values(self):
        other = replace(ERRAND, config_directory='example-assistant', settings_schema=None)
        with tempfile.TemporaryDirectory() as directory:
            with patch('errand.product.sys.platform', 'linux'):
                with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
                    first = Settings(path=ERRAND.settings_path())
                    second = Settings(path=other.settings_path())
                    first.save({'font-size': 17})
                    second.save({'font-size': 12})
                    self.assertEqual(Settings(path=first.path).get('font-size'), 17)
                    self.assertEqual(Settings(path=second.path).get('font-size'), 12)
