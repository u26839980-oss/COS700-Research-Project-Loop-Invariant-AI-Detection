"""Atomic JSON checkpoints with bounded retries for Windows file locks."""
import json
import os
from pathlib import Path
import tempfile
import time


def save(path, value, *, attempts=30):
    path = Path(path)
    if attempts < 1:
        raise ValueError("At least one save attempt is required")
    if path.parent.name == "trials" and path.exists():
        raise FileExistsError("Refusing to overwrite a completed trial: " + str(path))
    payload = json.dumps(value, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(min(0.05 * 2 ** attempt, 1.0))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
