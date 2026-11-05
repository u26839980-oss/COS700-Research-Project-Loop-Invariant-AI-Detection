"""Render checkpointed pilot evidence without running or reclassifying any models."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.infrastructure.code2inv_trace_sampler import validate_smt_observations
from backend.infrastructure.source_benchmark_repository import SourceBenchmarkRepository
from backend.application.source_pilot_metrics import benchmark_role
from scripts.compare_source_trace_audits import compare


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def detect_source_drift(folder, manifest):
    source_drift = [name for name, digest in manifest["source_hashes"].items()
                    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    amendment_path = folder / "schedule_amendment.json"
    amendment = load(amendment_path) if amendment_path.exists() else None
    runner = str(Path("scripts/run_source_pilot.py"))
    if amendment and amendment["original_runner_sha256"] == manifest["source_hashes"][runner] and (
            hashlib.sha256((ROOT / runner).read_bytes()).hexdigest() == amendment["updated_runner_sha256"]):
        source_drift = [name for name in source_drift if name != runner]
    return source_drift


def report(folder, *, audit_traces=False):
    manifest = load(folder / "manifest.json")
    source_drift = detect_source_drift(folder, manifest)
    amendment_path = folder / "schedule_amendment.json"
    amendment = load(amendment_path) if amendment_path.exists() else None
    if audit_traces:
        repository = SourceBenchmarkRepository()
        for budget in manifest["budgets"]:
            out = folder / f"trace_audit_{budget}"
            out.mkdir(exist_ok=True)
            tasks = []
            for task_id in manifest["task_ids"]:
                path = folder / f"trace_{task_id}_{budget}.json"
                record = load(path)
                row = dict(task_id=task_id, log_path=str(path.resolve()))
                if repository.task(task_id)["collection"] == "Code2Inv":
                    row["independent_SMT_rechecks"] = validate_smt_observations(
                        repository.read(task_id, "verification_conditions"), record["raw"]["runs"])
                tasks.append(row)
            summary = dict(options=record["options"], source_sha256=manifest["source_hashes"],
                           source_drift=bool(source_drift), tasks=tasks)
            (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        pairs = [compare(folder / f"trace_audit_{a}", folder / f"trace_audit_{b}")
                 for a, b in zip(manifest["budgets"], manifest["budgets"][1:])]
        (folder / "trace_pairing_audit.json").write_text(json.dumps(pairs, indent=2), encoding="utf-8")
        if not all(pair["all_passed"] for pair in pairs):
            raise ValueError("Increasing budgets changed sampled executions or failed to retain earlier rows")
    rows = [load(path) for path in sorted((folder / "trials").glob("*.json"))]
    lines = ["# Source Benchmark Pilot", "",
        f"Completed: {len(rows)} / {manifest['planned']} scheduled trials; actual model calls: {sum(row['model_calls'] for row in rows)}.",
        "", "One generation per treatment, temperature 0, seed 0. No consistency or statistical-significance claim.",
        "Trace budgets: 5, 10, 15, 20, 25 observations per loop per sampled input; three inputs. Syntax-only is run once.",
        "No reference invariants or checker certificates are sent to the models.",
        "Code2Inv checks at most the first eight parsed candidates independently; a full-proof trial has at least one sufficient candidate. Native C checks the submitted loop-contract bundle.",
        "Unsafe controls and the stronger native-safety profile mismatch are reported separately from positive tasks.",
        f"Code drift relative to the frozen manifest: {source_drift or 'none detected'}.", ""]
    if amendment:
        lines += ["Authorized scheduler amendment: smaller models first, then remaining Gemma trials. "
                  "Existing trial IDs and completed records are retained; generation and checker settings are unchanged.",
                  "Model order: " + " -> ".join(amendment["model_order"]), ""]
    batch_path = folder / "context_batch_amendment.json"
    if batch_path.exists():
        batch = load(batch_path)
        lines += ["Context-batched execution: group the same existing model/context settings to reduce reload overhead. "
                  f"Preserved {len(batch['preserved_trial_sha256'])} completed records. "
                  "Each remaining eligible cell still gets its own inference call. No prompts, token limits, contexts or checker limits changed. "
                  "Loading/cache effects make latency comparisons across execution phases uncontrolled.", ""]
    prompt_paths = sorted((folder / "prompts").glob("*.txt"))
    prompt_digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in prompt_paths}
    inventory = load(folder / "prompt_inventory.json")
    lines += ["## Evidence and Context", "",
        f"{len(prompt_paths)} treatment prompt files; {len(prompt_digests)} distinct prompt byte sequences. "
        "Some traces saturate before the requested row budget; identical evidence is not additional information.",
        "Context eligibility below is conservative UTF8-byte preflight, not measured tokenizer capacity. "
        "The 32768 context cap is a resource policy, not Gemma's advertised architectural limit.", ""]
    for model in manifest["models"]:
        cap = min(manifest["context_cap"], model["details"]["context_length"])
        count = sum(row["conservative_context"] > cap for row in inventory if row["conservative_context"])
        lines.append(f"- {model['name']}: {count}/462 context-preflight exclusions; context cap {cap}.")
    lines += ["", "| Example | Observed states at budgets 5 / 10 / 15 / 20 / 25 |", "|---|---|"]
    for task_id in manifest["task_ids"]:
        counts = []
        for budget in manifest["budgets"]:
            trace = load(folder / f"trace_{task_id}_{budget}.json")
            counts.append(sum(len(run["observations"]) for run in trace["evidence"]["runs"]))
        lines.append(f"| {task_id} | {' / '.join(map(str, counts))} |")
    lines.append("")
    for model in manifest["models"]:
        name = model["name"]
        mine = [row for row in rows if row["model"] == name]
        lines += [f"## {name}", "", f"Completed {len(mine)}/462. Digest: `{model['digest']}`.", "",
            "| Example | Role | Syntax | Trace 5/10/15/20/25 | Combined 5/10/15/20/25 |",
            "|---|---|---|---|---|"]
        for task_id in manifest["task_ids"]:
            subset = [row for row in mine if row["task_id"] == task_id]
            by_key = {(row["mode"], row["budget"]): row["outcome"] for row in subset}
            trace = " / ".join(by_key.get(("trace-only", b), "pending") for b in manifest["budgets"])
            combined = " / ".join(by_key.get(("syntax-plus-trace", b), "pending") for b in manifest["budgets"])
            role = benchmark_role(task_id)
            lines.append(f"| {task_id} | {role} | {by_key.get(('syntax-only', None), 'pending')} | {trace} | {combined} |")
        lines += ["", "Outcome counts by benchmark role:", ""]
        for role in ("positive", "unsafe_control", "native_safety_profile_mismatch"):
            counts = Counter(row["outcome"] for row in mine if row["benchmark_role"] == role)
            lines.append(f"- {role}: {dict(counts)}")
        candidate_counts = Counter(check["outcome_kind"] for row in mine for check in row.get("verifications", []))
        lines.append(f"- Code2Inv candidate-level outcomes (including rejected alternatives in successful/partial trials): {dict(candidate_counts)}")
        lines.append("")
    lines += ["## Interpretation", "",
        "- full_proof: a submitted candidate passed all declared safety/invariant obligations.",
        "- inductive_insufficient: induction proved, but the safety goal did not follow.",
        "- model_candidate_failure: candidates refuted or outside the state signature.",
        "- model_output_format_failure: no candidate in the requested format.",
        "- generation_truncated: output limit reached before a usable result; not a clean reasoning assessment.",
        "- candidate_language_or_schema_rejected: admission failed; inspect the saved diagnostic before attribution.",
        "- checker_limitation_or_inconclusive: unknown/unsupported/error/resource-limited proof, not a demonstrated wrong invariant.",
        "- context_limit / sampling_limitation: no model call was made for that cell.",
        "- provider_or_resource_failure: inference failed; not a demonstrated reasoning failure.",
        "- native_safety_failure: inspect the native obligation and benchmark role; not automatically a model error.",
        "", "Exact prompts: `prompts/`. Every raw output and checker record: `trials/`. Live totals: `progress.json`."]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(dict(completed=len(rows), planned=manifest["planned"],
                          outcomes=dict(Counter(row["outcome"] for row in rows)))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--audit-traces", action="store_true")
    args = parser.parse_args()
    report(args.folder.resolve(), audit_traces=args.audit_traces)
