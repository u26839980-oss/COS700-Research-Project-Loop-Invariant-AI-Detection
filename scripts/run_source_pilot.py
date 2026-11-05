"""Checkpointed real local-model source pilot. No fixtures or reference answers."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap import build_source_benchmark_service
from backend.application.source_pilot_metrics import benchmark_role, classify, summarize
from backend.domain.source_traces import TraceSamplingOptions, trace_evidence_digest
from backend.infrastructure.model_adapter import ModelAdapter
from backend.infrastructure.candidate_parser import extract_strict_native_contracts, extract_native_contracts


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def code_hashes():
    paths = [*ROOT.joinpath("backend").rglob("*.py"), *ROOT.joinpath("backend").rglob("*.c"),
             Path(__file__), ROOT / "bootstrap.py"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def execution_order(jobs, models, smaller_first=False):
    if not smaller_first:
        return list(range(len(jobs)))
    ordered = sorted(models, key=lambda item: ("gemma" in item["name"].lower(), item["size"]))
    ranks = {item["name"]: rank for rank, item in enumerate(ordered)}
    return sorted(range(len(jobs)), key=lambda index: (ranks[jobs[index]["model"]], index))


def validate_resume(folder, specification, smaller_first):
    original = read(folder / "manifest.json")
    if original == specification:
        return
    runner = str(Path("scripts/run_source_pilot.py"))
    permitted = json.loads(json.dumps(specification))
    permitted["source_hashes"][runner] = original["source_hashes"][runner]
    if not smaller_first or original != permitted:
        raise ValueError("Frozen code/models/settings changed beyond the authorized scheduler revision")
    snapshot = folder / "scheduler_original.py"
    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != original["source_hashes"][runner]:
        raise ValueError("Original scheduler snapshot does not match the frozen manifest")
    amendment = dict(reason="Prioritize smaller local models while preserving completed treatments.",
        original_runner_sha256=original["source_hashes"][runner],
        updated_runner_sha256=specification["source_hashes"][runner],
        policy="Execution order only. Original trial IDs, prompts, models, settings, traces and completed records are unchanged.",
        model_order=[item["name"] for item in sorted(specification["models"],
            key=lambda item: ("gemma" in item["name"].lower(), item["size"]))])
    path = folder / "schedule_amendment.json"
    if path.exists() and read(path) != amendment:
        raise ValueError("Scheduler amendment changed")
    if not path.exists():
        save(path, amendment)
    preservation = folder / "checkpoint_preservation.json"
    if preservation.exists():
        for name, digest in read(preservation).items():
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest:
                raise ValueError("A preserved trial was modified")
    else:
        save(preservation, {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted((folder / "trials").glob("*.json"))})


def inventory(adapter):
    url = adapter.ollama.base_url
    if urllib.parse.urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Pilot permits only a loopback Ollama endpoint")
    with urllib.request.urlopen(url + "/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    if not models or not any("gemma" in item["name"].lower() for item in models):
        raise ValueError("All local models, including installed Gemma, are required")
    if any(item.get("remote_host") or "cloud" in item["name"] for item in models):
        raise ValueError("Remote/cloud aliases are not eligible")
    return [{key: item[key] for key in ("name", "digest", "size", "details")} for item in models]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--trace-source", type=Path)
    parser.add_argument("--gemma-base-url", default="http://localhost:11434")
    parser.add_argument("--smaller-models-first", action="store_true")
    parser.add_argument("--native-response-format", choices=["original", "corrected"], default="original",
                        help="Keep the historical direct-JSON policy, or explicitly admit a single JSON code fence.")
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    native_parser = extract_strict_native_contracts if args.native_response_format == "original" else extract_native_contracts
    service = build_source_benchmark_service(native_contract_parser=native_parser)
    service.repository.results = folder / "evidence"
    adapter = ModelAdapter()
    models = inventory(adapter)
    if urllib.parse.urlparse(args.gemma_base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Gemma also requires a loopback endpoint")
    with urllib.request.urlopen(args.gemma_base_url.rstrip("/") + "/api/tags", timeout=10) as response:
        gemma_tags = {item["name"]: item["digest"] for item in json.load(response)["models"]}
    if any(gemma_tags.get(item["name"]) != item["digest"] for item in models if "gemma" in item["name"]):
        raise ValueError("Gemma endpoint does not have the frozen local artifact")
    tasks = service.repository.catalog()
    if len(tasks) != 42:
        raise ValueError("Expected the frozen 42-task source cohort")
    manifest_path = folder / "manifest.json"
    specification = dict(schema="source_pilot_v1", native_response_format=args.native_response_format,
        source_hashes=code_hashes(), models=models,
        task_ids=[task["id"] for task in tasks], budgets=[5, 10, 15, 20, 25],
        modes=["syntax-only", "trace-only", "syntax-plus-trace"], repetitions=1,
        generation=dict(temperature=0, seed=0, max_output_tokens=2048, timeout_seconds=180),
        checker=dict(timeout=90, unwind=20), context_cap=32768,
        context_policy="Round conservative UTF8-byte requirement up to 1024, minimum4096; cap at declared model context or32768. Never silently truncate input.",
        sampling=asdict(TraceSamplingOptions()), planned=42 * len(models) * 11,
        trace_source=str(args.trace_source.resolve()) if args.trace_source else None,
        endpoints={item["name"]: args.gemma_base_url.rstrip("/") if "gemma" in item["name"] else adapter.ollama.base_url for item in models},
        timeout_policy="Gemma CPU recovery: 600 seconds per call; others180. No cross-hardware latency ranking.",
        offline_policy="Loopback Ollama, fresh single-turn requests, no tools/search/history. OS network isolation and lack of training contamination are not claimed.")
    if manifest_path.exists():
        validate_resume(folder, specification, args.smaller_models_first)
    else:
        save(manifest_path, specification)
    traces = {}
    for budget in specification["budgets"]:
        for task in tasks:
            task_id = task["id"]
            path = folder / f"trace_{task_id}_{budget}.json"
            if path.exists():
                record = read(path)
            elif args.trace_source:
                original_manifest = read(args.trace_source / "manifest.json")
                sampler_files = ("backend/domain/source_traces.py", "backend/infrastructure/source_trace_sampler.py",
                    "backend/infrastructure/code2inv_trace_sampler.py", "backend/infrastructure/native_trace_sampler.py",
                    "backend/infrastructure/native_trace_instrumentation.py", "backend/infrastructure/native_trace_runtime.c")
                for name in sampler_files:
                    key = str(Path(name))
                    if original_manifest["source_hashes"][key] != specification["source_hashes"][key]:
                        raise ValueError("Cannot reuse traces after sampler implementation changes")
                record = read(args.trace_source / path.name)
                save(path, record)
            else:
                record = service.sample_traces(task_id, TraceSamplingOptions(rows_per_loop=budget))
                save(path, record)
            if trace_evidence_digest(record["evidence"]) != record["evidence_sha256"]:
                raise ValueError("Trace evidence drift")
            traces[task_id, budget] = record
        print(f"Prepared traces: budget={budget}, tasks=42", flush=True)
    jobs = []
    # Model-major execution avoids repeatedly loading 20GB Gemma weights.
    for model in models:
        for task in tasks:
            for mode in specification["modes"]:
                for budget in ([None] if mode == "syntax-only" else specification["budgets"]):
                    jobs.append(dict(model=model["name"], task_id=task["id"], mode=mode, budget=budget,
                                     benchmark_role=benchmark_role(task["id"])))
    prompt_dir = folder / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    prompts = {}
    for job in jobs:
        key = (job["task_id"], job["mode"], job["budget"])
        if key in prompts:
            continue
        trace = traces.get((job["task_id"], job["budget"]))
        if trace and trace["status"] != "sampled_all_loops":
            prompts[key] = None
            continue
        prompt = service.prompt(job["task_id"], input_mode=job["mode"], trace_record=trace)
        path = prompt_dir / ("_".join(map(str, key)) + ".txt")
        if path.exists() and path.read_text(encoding="utf-8") != prompt:
            raise ValueError("Frozen prompt changed")
        path.write_text(prompt, encoding="utf-8")
        prompts[key] = prompt
    save(folder / "prompt_inventory.json", [dict(task_id=k[0], mode=k[1], budget=k[2],
        bytes=len(v.encode()) if v else None,
        conservative_context=service.conservative_context_requirement(v, 2048) if v else None)
        for k, v in prompts.items()])
    print(f"Frozen {len(jobs)} trials, {len(prompts)} unique prompts", flush=True)
    if args.prepare_only:
        return
    # A durable worker may be left running; never permit two writers to its ledger.
    lease = (folder / "worker.lock").open("a+b")
    lease.write(b"0")
    lease.flush()
    lease.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    results = folder / "trials"
    results.mkdir(exist_ok=True)
    completed = {}
    for index, job in enumerate(jobs):
        path = results / f"{index:05d}.json"
        if path.exists():
            record = read(path)
            if any(record[key] != value for key, value in job.items()):
                raise ValueError("Checkpoint trial does not match frozen design")
            completed[index] = {key: record[key] for key in (*job.keys(), "outcome", "model_calls", "elapsed_seconds")}
    for index in execution_order(jobs, models, args.smaller_models_first):
        job = jobs[index]
        if (folder / "STOP_AFTER_CURRENT").exists():
            print("Stopped at a completed-trial checkpoint by request.", flush=True)
            return
        if index in completed:
            continue
        path = results / f"{index:05d}.json"
        if path.exists():
            record = read(path)
            if any(record[key] != value for key, value in job.items()):
                raise ValueError("Checkpoint trial does not match frozen design")
        else:
            prompt = prompts[job["task_id"], job["mode"], job["budget"]]
            model = next(item for item in models if item["name"] == job["model"])
            maximum = min(specification["context_cap"], model["details"]["context_length"])
            required = service.conservative_context_requirement(prompt, 2048) if prompt else 0
            context = max(4096, math.ceil(required / 1024) * 1024)
            started = time.monotonic()
            if prompt is None:
                record = dict(status="sampling_limitation", model_calls=0)
            elif context > maximum:
                record = dict(status="context_limit", model_calls=0, required_context=required, available_context=maximum)
            else:
                print(f"CALL trial_id={index} completed={len(completed)}/{len(jobs)} {job}", flush=True)
                try:
                    adapter.ollama.base_url = specification["endpoints"][job["model"]]
                    settings = dict(specification["generation"])
                    if "gemma" in job["model"]:
                        settings["timeout_seconds"] = 600
                    record = service.generate(job["task_id"], job["model"], adapter,
                        input_mode=job["mode"], trace_record=traces.get((job["task_id"], job["budget"])),
                        context_length=context, **settings, **specification["checker"])
                except Exception as exc:
                    record = dict(status="execution_error", model_calls=1, diagnostic=repr(exc),
                        generation=dict(metadata=dict(adapter.last_generation_metadata)))
            record.update(**job, elapsed_seconds=time.monotonic()-started)
            record["outcome"] = classify(record)
            save(path, record)
        completed[index] = {key: record[key] for key in (*job.keys(), "outcome", "model_calls", "elapsed_seconds")}
        rows = [completed[key] for key in sorted(completed)]
        save(folder / "progress.json", summarize(rows, len(jobs)))
        save(folder / "trial_index.json", rows)
        print(f"DONE {len(rows)}/{len(jobs)} trial_id={index} {record['outcome']} {record['elapsed_seconds']:.1f}s", flush=True)
    rows = [completed[key] for key in sorted(completed)]
    save(folder / "summary.json", summarize(rows, len(jobs)))


if __name__ == "__main__":
    main()
