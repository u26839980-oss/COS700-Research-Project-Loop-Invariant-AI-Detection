from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any
from backend.domain.models import VerificationResult


class ExperimentRepository:
    def __init__(self, path: str | None = None) -> None:
        if path is None:
            root = Path(__file__).resolve().parents[2]
            path = str(root / "results" / "experiment_runs.jsonl")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict[str, Any]) -> str:
        record = {
            "schema_version": 3,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=self._default) + "\n")
        return str(self.path)

    def _default(self, value: object) -> object:
        if isinstance(value, VerificationResult):
            return value.to_record()
        if is_dataclass(value):
            return {item.name: getattr(value, item.name) for item in fields(value)}
        return str(value)
