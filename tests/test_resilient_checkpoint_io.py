import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.resilient_checkpoint_io import save


class ResilientCheckpointTests(unittest.TestCase):
    def test_retries_replace_without_changing_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.json"
            path.write_text('{"old":true}')
            import os
            replace = os.replace
            calls = []
            def locked(source, target):
                calls.append(source)
                if len(calls) < 3:
                    self.assertEqual(json.loads(path.read_text()), {"old": True})
                    raise PermissionError("Windows sharing violation")
                replace(source, target)
            with patch('scripts.resilient_checkpoint_io.os.replace', side_effect=locked), patch('scripts.resilient_checkpoint_io.time.sleep'):
                save(path, {"completed": 451})
            self.assertEqual(json.loads(path.read_text()), {"completed": 451})
            self.assertEqual(len(calls), 3)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_permanent_lock_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            path.write_bytes(b'{"completed":450}')
            with patch('scripts.resilient_checkpoint_io.os.replace', side_effect=PermissionError), patch('scripts.resilient_checkpoint_io.time.sleep'):
                with self.assertRaises(PermissionError):
                    save(path, {"completed": 451}, attempts=2)
            self.assertEqual(path.read_bytes(), b'{"completed":450}')

    def test_existing_trial_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            trials = Path(folder) / 'trials'
            trials.mkdir()
            path = trials / '00000.json'
            path.write_bytes(b'{"actual":"preserved"}')
            with self.assertRaises(FileExistsError):
                save(path, {"replacement": True})
            self.assertEqual(path.read_bytes(), b'{"actual":"preserved"}')

    def test_new_trial_writes_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trial.json'
            save(path, {"actual": [1, 2, 3]})
            self.assertEqual(json.loads(path.read_text()), {"actual": [1, 2, 3]})


if __name__ == '__main__':
    unittest.main()
