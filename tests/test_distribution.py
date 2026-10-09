import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_macos_release import download


class DistributionTests(unittest.TestCase):
    def test_insecure_source_download_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                download('http://example.com/source.tar.xz', Path(directory) / 'source.tar.xz')

    def test_source_checksum_is_verified_before_accepting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'source.tar.xz'
            expected = hashlib.sha256(b'expected source').hexdigest()
            with patch('scripts.build_macos_release.urlopen', return_value=io.BytesIO(b'wrong source')):
                with self.assertRaises(ValueError):
                    download('https://example.com/source.tar.xz', destination, expected)
            self.assertFalse(destination.exists())
            self.assertFalse(list(Path(directory).glob('*.partial')))

    def test_verified_cache_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'source.tar.xz'
            destination.write_bytes(b'verified source')
            expected = hashlib.sha256(destination.read_bytes()).hexdigest()
            with patch('scripts.build_macos_release.urlopen') as request:
                self.assertEqual(download('https://example.com/source.tar.xz', destination, expected), destination)
                request.assert_not_called()
