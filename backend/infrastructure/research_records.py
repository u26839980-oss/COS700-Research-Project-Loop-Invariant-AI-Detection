"""Strict JSONL reads; only an incomplete, unterminated live tail may be ignored."""
import json
from pathlib import Path


def read_records(path: Path, *, allow_partial_tail: bool = False) -> tuple[list[dict], bool]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    records = []
    partial_tail = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if allow_partial_tail and index == len(lines) - 1 and not source.endswith("\n"):
                partial_tail = True
                break
            raise ValueError(f"Invalid JSONL record at line {index + 1} in {path.name}.") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Expected an object at line {index + 1} in {path.name}.")
        records.append(record)
    return records, partial_tail
