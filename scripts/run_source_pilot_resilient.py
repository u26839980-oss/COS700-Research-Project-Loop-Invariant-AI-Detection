"""Execution-only save retry wrapper; frozen generation and scheduling stay intact."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_source_pilot_context_batches as batching
from scripts import resilient_checkpoint_io


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_amendment(folder):
    path = folder / "persistence_amendment.json"
    code = [Path(__file__), Path(resilient_checkpoint_io.__file__)]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in code}
    if path.exists():
        amendment = batching.frozen.read(path)
        if amendment["execution_code_sha256"] != hashes:
            raise ValueError("Persistence execution code changed")
        if amendment["manifest_sha256"] != sha(folder / "manifest.json"):
            raise ValueError("Frozen manifest changed")
        for name, digest in amendment["preserved_trial_sha256"].items():
            if sha(folder / name) != digest:
                raise ValueError("Completed trial changed or missing: " + name)
        return amendment
    amendment = dict(reason="Use atomic checkpoint persistence with bounded Windows file-lock retries.",
        policy="Persistence only: unique same-directory temporary files, fsync and bounded PermissionError retries. No inference, prompt, checker, context, model order or test-setting changes. Completed trials cannot be overwritten.",
        execution_code_sha256=hashes, manifest_sha256=sha(folder / "manifest.json"),
        preserved_trial_sha256={str(p.relative_to(folder)): sha(p)
            for p in sorted((folder / "trials").glob("*.json"))})
    resilient_checkpoint_io.save(path, amendment)
    return amendment


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    amendment = record_amendment(args.output.resolve())
    original_save = batching.frozen.save
    batching.frozen.save = resilient_checkpoint_io.save
    print("Windows save retries enabled; preserved trials=" + str(len(amendment["preserved_trial_sha256"])), flush=True)
    try:
        batching.main()
    finally:
        batching.frozen.save = original_save


if __name__ == "__main__":
    main()
