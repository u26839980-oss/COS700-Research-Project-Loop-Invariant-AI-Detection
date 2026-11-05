"""Execution-only adapter around the immutable pilot runner; never reuse answers."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_source_pilot as frozen
from backend.application.source_benchmark_service import SourceBenchmarkService


def requested_context(required):
    return max(4096, math.ceil((required or 0) / 1024) * 1024)


def grouped_order(jobs, baseline_indices, contexts):
    """Retain model priority and stable trial IDs, grouping only identical contexts."""
    ranks = {}
    for index in baseline_indices:
        ranks.setdefault(jobs[index]["model"], len(ranks))
    return sorted(baseline_indices, key=lambda index: (
        ranks[jobs[index]["model"]],
        contexts[jobs[index]["task_id"], jobs[index]["mode"], jobs[index]["budget"]], index))


def load_contexts(folder, output_tokens):
    inventory = frozen.read(folder / "prompt_inventory.json")
    contexts = {}
    for row in inventory:
        key = row["task_id"], row["mode"], row["budget"]
        if key in contexts:
            raise ValueError("Duplicate frozen prompt inventory key")
        if row["conservative_context"] is not None:
            prompt = (folder / "prompts" / ("_".join(map(str, key)) + ".txt")).read_text(encoding="utf-8")
            required = SourceBenchmarkService.conservative_context_requirement(prompt, output_tokens)
            if required != row["conservative_context"]:
                raise ValueError("Prompt context does not match the frozen inventory")
        contexts[key] = requested_context(row["conservative_context"])
    return contexts


def record_amendment(folder):
    path = folder / "context_batch_amendment.json"
    wrapper_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runner_hash = hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest()
    if path.exists():
        amendment = frozen.read(path)
        if amendment["wrapper_sha256"] != wrapper_hash or amendment["frozen_runner_sha256"] != runner_hash:
            raise ValueError("Execution adapter or frozen runner changed")
        for name, digest in amendment["preserved_trial_sha256"].items():
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                raise ValueError("A completed trial was changed or lost")
        return amendment
    hashes = {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((folder / "trials").glob("*.json"))}
    amendment = dict(reason="Group configured context sizes while preserving test settings and completed records.",
        policy="Keep smaller-model priority. Within each model, group existing requested context sizes. Each pending eligible cell still makes a separate real inference call. No response reuse, deduplication, token changes, concurrency or early stopping.",
        wrapper_path=str(Path(__file__).relative_to(ROOT)), wrapper_sha256=wrapper_hash,
        frozen_runner_sha256=runner_hash, preserved_trial_sha256=hashes,
        frozen_manifest_sha256=hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(),
        timing_caveat="Execution order changes loading/cache opportunities. Do not interpret lower latency as improved model reasoning, and do not claim bitwise-identical future responses solely from temperature zero.")
    frozen.save(path, amendment)
    return amendment


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    folder = args.output.resolve()
    if (folder / "STOP_AFTER_CURRENT").exists():
        raise ValueError("The checkpoint stop request is still present")
    manifest = frozen.read(folder / "manifest.json")
    contexts = load_contexts(folder, manifest["generation"]["max_output_tokens"])
    amendment = record_amendment(folder)
    baseline_order = frozen.execution_order

    def scheduled(jobs, models, smaller_first=False):
        return grouped_order(jobs, baseline_order(jobs, models, smaller_first), contexts)

    # The frozen entrypoint looks up this scheduling collaborator at runtime.
    # No generator, checker, parser or persistence collaborator is replaced.
    frozen.execution_order = scheduled
    print("Context-batched scheduling; preserved trials=" + str(len(amendment["preserved_trial_sha256"])), flush=True)
    try:
        frozen.main()
    finally:
        frozen.execution_order = baseline_order


if __name__ == "__main__":
    main()
