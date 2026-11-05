"""Hash-checked source catalog and append-only, no-timestamp local records."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]


class SourceBenchmarkRepository:
    def __init__(self, bundle=None, results=None):
        self.bundle = Path(bundle) if bundle else ROOT / "benchmarks/source_verified_v1"
        self.results = Path(results) if results else ROOT / "results/source_benchmarks"
        self.manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        self.tasks = {task["id"]: task for task in self.manifest["tasks"]}

    def catalog(self):
        return list(self.tasks.values())

    def task(self, task_id):
        return self.tasks[task_id]

    def read(self, task_id, key="primary_input"):
        task = self.task(task_id)
        relative = task[key]
        asset = next(asset for asset in task["assets"] if asset["path"] == relative)
        path = (self.bundle / relative).resolve()
        if not path.is_relative_to(self.bundle.resolve()):
            raise ValueError("Source path escapes bundle")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
            raise ValueError("Source integrity check failed: " + task_id)
        return raw.decode("utf-8")

    def reserve(self):
        self.results.mkdir(parents=True, exist_ok=True)
        path = self.results / ("check_" + uuid4().hex)
        return path

    def save(self, folder, record):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "workbench.json"
        with path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        return str(path)
