"""Benchmark setup checks use a temporary cache, never the network."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.fetch_benchmarks import fetch, within


class BenchmarkFetchTests(unittest.TestCase):
    def fixture(self, folder):
        data=b'int main(void) { return 0; }\n'
        cache=folder/'cache'
        cache.mkdir()
        (cache/'example.c').write_bytes(data)
        asset=dict(path='example.c',repository='code2inv',commit='a'*40,upstream_path='examples/example.c',
                   bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        manifest=folder/'manifest.json'
        manifest.write_text(json.dumps(dict(source_pins={'code2inv':dict(
            repository='https://github.com/PL-ML/code2inv',commit='a'*40)},
            tasks=[dict(assets=[asset])],provenance_assets=[])))
        return manifest,cache

    def test_verified_cache_and_read_only_verification_make_no_network_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            manifest,cache=self.fixture(folder)
            destination=folder/'output'
            with patch('scripts.fetch_benchmarks.urlopen',side_effect=AssertionError('No network')):
                result=fetch(manifest,destination,cache=cache)
                self.assertEqual(result['verified_assets'],1)
                self.assertEqual(fetch(manifest,destination,verify=True),result)
                self.assertEqual(result['model_calls'],0)

    def test_corrupt_asset_is_rejected_without_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            manifest,cache=self.fixture(folder)
            destination=folder/'output'
            fetch(manifest,destination,cache=cache)
            target=destination/'example.c'
            target.write_text('changed')
            with self.assertRaises(ValueError):
                fetch(manifest,destination,cache=cache)
            self.assertEqual(target.read_text(),'changed')

    def test_paths_cannot_escape_bundle(self):
        for relative in ['../outside','/absolute','C:/outside','folder\\file']:
            with self.subTest(relative=relative),self.assertRaises(ValueError):
                within(Path.cwd(),relative)
