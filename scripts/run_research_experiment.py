from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import random
import sys
import time
import urllib.request
from copy import deepcopy
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain.models import ExecutionTrace
from backend.domain.textbook_contract import resolve_textbook_context, resolve_unassisted_context
from backend.domain.candidate_contract import (
    SCALAR_CANDIDATE_LIMIT, SCALAR_GENERATION_CONTRACT,
    TEXTBOOK_CANDIDATE_LIMIT, TEXTBOOK_GENERATION_CONTRACT,
)
from backend.application.hypothesis_generation import generate_hypotheses
from backend.application.hypothesis_verification import verify_hypotheses, verify_candidates
from backend.infrastructure.loop_parser import extract_syntax_features, parse_expression, parse_program
from backend.infrastructure.model_adapter import ModelAdapter
from backend.infrastructure.prompt_builder import PromptBuilder
from backend.infrastructure.research_benchmarks import TEMPLATES, scaling_case
from backend.infrastructure.trace_executor import SafeExpressionEvaluator, TraceExecutionError, TraceExecutor
from backend.infrastructure.z3_checker import Z3InvariantChecker
from backend.infrastructure.benchmark_z3_checker import BenchmarkZ3Checker
from backend.infrastructure.textbook_runtime import execute as execute_textbook, evaluate as evaluate_textbook


from backend.application.research_metrics import summarize, wilson
from backend.application.assessment import assess_candidates, has_unresolved_checker_work


MODES = ["syntax-only", "trace-only", "syntax-plus-trace"]
FIELDS = [
    "run_id", "trial_id", "attempt_id", "repetition", "repair_index", "seed",
    "experiment_name", "example_name", "example_template", "source", "source_status",
    "bound_mode", "bound_value", "benchmark_complete", "verification_backend",
    "trace_complete", "trace_rows", "trace_file", "model_name", "provider",
    "generation_metadata", "input_mode", "prompt_style", "temperature",
    "trace_iteration_limit", "program_text", "precondition", "postcondition",
    "expected_invariant", "prompt", "prompt_sha256", "prompt_refresh_id", "raw_output",
    "candidate_index", "candidate", "parseable", "accepted", "establishment", "preservation",
    "postcondition_result", "counterexamples", "failure_category", "error",
    "duration_seconds", "verification_seconds", "protocol_version", "generation_strategy", "model_calls",
    "candidate_policy", "candidate_origin", "candidate_origins",
    "assessment", "is_inductive", "proves_postcondition", "obligations_proved",
    "verification_contract", "proof_context", "effective_candidate", "outcome_kind", "limitation_scope",
    "candidate_contribution",
    "candidate_limit", "generation_contract",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Controlled repeated invariant experiments (protocol v3).")
    parser.add_argument("--suite", choices=["scaling", "examples", "all"], default="scaling")
    parser.add_argument("--textbook-only", action="store_true", help="Exclude local non-textbook controls from the examples suite.")
    parser.add_argument("--textbook-input-sizes", nargs="+", type=int,
                        help="Increasing positive array lengths / exponent n; programs and specifications stay unchanged.")
    parser.add_argument("--templates", nargs="+", choices=list(TEMPLATES), default=["transfer"])
    parser.add_argument("--models", nargs="+")
    parser.add_argument("--bounds", nargs="+", type=int, help="Explicit bounds; cannot be combined with range options.")
    parser.add_argument("--bound-start", type=int)
    parser.add_argument("--bound-stop", type=int)
    parser.add_argument("--bound-step", type=int)
    parser.add_argument("--bound-modes", nargs="+", choices=["constant", "symbolic"], default=["constant"])
    parser.add_argument("--input-modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--repeat-penalty", type=float)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--max-repairs", type=int, default=0)
    parser.add_argument("--generation-strategy", choices=["direct", "relations_first", "relations_only"], default="direct")
    parser.add_argument("--candidate-policy", choices=["independent", "conjoin_inductive"], default="independent")
    parser.add_argument("--trace-cap", type=int, default=11000)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--solver-timeout-ms", type=int, default=3000)
    parser.add_argument("--schedule", choices=["incremental", "model-blocked", "randomized"], default="incremental")
    parser.add_argument("--prompt-style", choices=["minimal_frontend", "original_few_shot", "guided_self_check"], default="minimal_frontend")
    parser.add_argument("--frontend-prompt-addition", default="")
    parser.add_argument("--include-trace-observations", action="store_true")
    parser.add_argument("--no-refresh", action="store_true", help="Compatibility flag; v2 never inserts random prompt markers.")
    parser.add_argument("--include-unsupported-model-calls", action="store_true", help="Capture unsupported examples' raw responses without formal scoring.")
    parser.add_argument("--output-dir", default=str(ROOT / "results" / "research_experiments"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.textbook_input_sizes and (any(size < 1 for size in args.textbook_input_sizes)
            or args.textbook_input_sizes != sorted(set(args.textbook_input_sizes))):
        parser.error("Textbook input sizes must be unique, positive and increasing.")
    if args.repeat_penalty is not None and not 0 < args.repeat_penalty <= 2:
        parser.error("repeat-penalty must be greater than 0 and at most 2.")
    range_values = (args.bound_start, args.bound_stop, args.bound_step)
    if args.bounds is not None:
        if any(value is not None for value in range_values):
            parser.error("Use either --bounds or --bound-start/--bound-stop/--bound-step.")
    else:
        args.bound_start = 0 if args.bound_start is None else args.bound_start
        args.bound_stop = 50 if args.bound_stop is None else args.bound_stop
        args.bound_step = 5 if args.bound_step is None else args.bound_step
        if args.bound_step <= 0 or args.bound_start < 0 or args.bound_stop < args.bound_start:
            parser.error("Require 0 <= bound-start <= bound-stop and bound-step > 0.")
        if (args.bound_stop - args.bound_start) % args.bound_step:
            parser.error("bound-stop must be reachable from bound-start using bound-step.")
        args.bounds = list(range(args.bound_start, args.bound_stop + 1, args.bound_step))
    if min(args.repeats, args.max_output_tokens, args.context_length, args.trace_cap,
           args.timeout_seconds, args.solver_timeout_ms) < 1 or args.max_repairs < 0:
        parser.error("Counts and timeouts must be positive; repairs must be nonnegative.")
    if args.seed < 0 or args.seed + args.repeats + args.max_repairs > 2147483647:
        parser.error("Seeds must fit a nonnegative signed 32-bit integer.")
    if any(bound < 0 for bound in args.bounds) or not 0 <= args.temperature <= 1:
        parser.error("Bounds must be nonnegative; temperature must be between 0 and 1.")
    for name in ("bounds", "models", "templates", "input_modes", "bound_modes"):
        values = getattr(args, name)
        if values and len(values) != len(set(values)):
            parser.error(f"Duplicate values in --{name.replace('_', '-')} are not allowed.")
    return args


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_cases(args):
    cases = []
    if args.suite in ("scaling", "all"):
        for template in args.templates:
            for mode in args.bound_modes:
                for bound in args.bounds:
                    cases.append(scaling_case(template, bound, mode))
    if args.suite in ("examples", "all"):
        from backend.domain.benchmarks import EXAMPLES
        for index, (name, example) in enumerate(EXAMPLES.items()):
            if args.textbook_only and not example.get("book_benchmark"):
                continue
            cases.append({
                "case_id": f"ui-{index}", "experiment_name": "ready_made_examples_v2",
                "example_name": name, "example_template": name,
                "source": example.get("source", ""), "source_status": example.get("source_status", ""),
                "bound_mode": "fixed", "bound_value": "", "benchmark_complete": "yes" if example.get("benchmark_complete") else "no",
                "book_benchmark": "yes" if example.get("book_benchmark") else "no",
                "program_text": example["program"], "precondition": example["precondition"],
                "postcondition": example["postcondition"], "expected_invariant": example["invariant"],
                "initial_environment": example.get("sample_inputs", {}),
                "conversion_note": example.get("conversion_note", ""),
                "source_photo": example.get("source_photo", ""),
                "loop_head_bounds": example.get("loop_head_bounds", "True"),
                "input_domain_notes": example.get("input_domain_notes", ""),
            })
    if args.textbook_input_sizes:
        from backend.domain.benchmarks import EXAMPLES
        from backend.infrastructure.textbook_input_scaling import textbook_inputs_at_size
        expanded = []
        for case in cases:
            if case["book_benchmark"] != "yes":
                expanded.append(case)
                continue
            for size in args.textbook_input_sizes:
                sized = deepcopy(case)
                sized.update(case_id=f"{case['case_id']}-size-{size}", bound_mode="input_size", bound_value=size,
                             initial_environment=textbook_inputs_at_size(case["example_name"], EXAMPLES[case["example_name"]], size))
                expanded.append(sized)
        cases = expanded
    for case in cases:
        context = (resolve_unassisted_context(case["program_text"], case["precondition"])
                   if case["book_benchmark"] == "yes" else None)
        case["verification_contract"] = context.version if context else "standalone_v1"
        case["proof_context"] = asdict(context) if context else {}
        case["candidate_limit"] = TEXTBOOK_CANDIDATE_LIMIT if context else SCALAR_CANDIDATE_LIMIT
        case["generation_contract"] = TEXTBOOK_GENERATION_CONTRACT if context else SCALAR_GENERATION_CONTRACT
    return cases


def verify_textbook_case(case, candidate, timeout_ms, *, reference=False):
    context = (resolve_textbook_context(case, case["program_text"], case["precondition"], case["postcondition"])
               if reference else resolve_unassisted_context(case["program_text"], case["precondition"]))
    return BenchmarkZ3Checker(timeout_ms=timeout_ms).verify(case["example_name"], candidate,
        program=case["program_text"], precondition=case["precondition"], postcondition=case["postcondition"],
        array_names=tuple(key for key, value in case["initial_environment"].items() if isinstance(value, list)),
        proof_context=context)


def collect_trace(parsed, initial, cap):
    executor = TraceExecutor()
    try:
        return executor.execute(parsed, initial, cap), "yes"
    except TraceExecutionError as exc:
        if "did not terminate within the iteration limit" not in str(exc):
            raise
    environment = dict(initial)
    for statement in parsed.prefix:
        executor._execute_statement(statement, environment)
    rows = [executor._trace_row(0, parsed.variables, environment)]
    for iteration in range(1, cap + 1):
        if not executor._evaluate_node(parsed.loop.test, environment):
            break
        for statement in parsed.loop.body:
            executor._execute_statement(statement, environment)
        rows.append(executor._trace_row(iteration, parsed.variables, environment))
    return ExecutionTrace(rows), "no"


def prepare_cases(cases, checker, args):
    prepared = {}
    for case in cases:
        data = {"case": case, "error": "", "trace": ExecutionTrace([]), "trace_complete": "unsupported"}
        prepared[case["case_id"]] = data
        if case["book_benchmark"] == "yes":
            reference = verify_textbook_case(case, case["expected_invariant"], args.solver_timeout_ms, reference=True)
            if reference.establishment == "unsupported":
                data["error"] = reference.error
                if args.include_unsupported_model_calls:
                    trace, final = execute_textbook(case["program_text"], case["initial_environment"],
                                                   case["precondition"], args.trace_cap)
                    data.update(textbook=True, discovery_only=True, trace=trace,
                                trace_complete="yes", reference=asdict(reference), final_state=final)
                continue
            if not reference.accepted:
                raise RuntimeError(f"Textbook reference preflight failed for {case['example_name']}: {reference}")
            trace, final = execute_textbook(case["program_text"], case["initial_environment"],
                                             case["precondition"], args.trace_cap)
            if not all(evaluate_textbook(case["expected_invariant"], row) for row in trace.rows):
                raise RuntimeError("Textbook reference disagrees with execution.")
            if not evaluate_textbook(case["postcondition"], final):
                raise RuntimeError("Textbook postcondition disagrees with execution.")
            data.update(textbook=True, trace=trace, trace_complete="yes", reference=asdict(reference),
                        final_state=final)
            continue
        try:
            parsed = parse_program(case["program_text"])
            reference = checker.verify(parsed, case["precondition"], case["expected_invariant"], case["postcondition"])
            if not reference.accepted:
                raise ValueError(f"Reference invariant failed preflight: {asdict(reference)}")
            trace, complete = collect_trace(parsed, case["initial_environment"], args.trace_cap)
            if not trace_holds(case["expected_invariant"], trace):
                raise ValueError("Reference invariant disagrees with concrete execution.")
            data.update(parsed=parsed, syntax=extract_syntax_features(parsed), trace=trace,
                        trace_complete=complete, reference=asdict(reference))
        except Exception as exc:
            # A broken numerical reference invalidates the experiment rather than the model.
            raise RuntimeError(f"Benchmark preflight failed for {case['example_name']}: {exc}") from exc
    return prepared


def trace_holds(candidate, trace):
    expression = parse_expression(candidate)
    return all(bool(SafeExpressionEvaluator(row).visit(expression)) for row in trace.rows)


def build_prompt(data, mode, args):
    case = data["case"]
    if data.get("textbook"):
        from backend.infrastructure.textbook_prompt_builder import build_book_benchmark_prompt
        return build_book_benchmark_prompt(case["example_name"], case, case["program_text"],
            case["precondition"], case["postcondition"], args.frontend_prompt_addition,
            args.prompt_style, "", True, [], mode, data["trace"])
    prompt = PromptBuilder().build(
        mode=mode, syntax_features=data["syntax"], trace=data["trace"],
        precondition=case["precondition"], postcondition=case["postcondition"],
        guidance_examples=[], frontend_prompt_addition=args.frontend_prompt_addition,
        prompt_style=args.prompt_style, prompt_refresh_id="",
        include_trace_observations=args.include_trace_observations,
    )
    if mode != "syntax-only":
        prompt += f"\nTRACE CAPTURE: {len(data['trace'].rows)} rows; completed={data['trace_complete']}.\n"
        prompt += "Long traces show the first 8 and last 5 recorded states; omitted rows are marked.\n"
    return prompt


def failure_category(raw, verifications, generation_metadata=None):
    informative = [item for item in verifications if item.has_model_contribution]
    if any(item.proves_postcondition for item in informative):
        return "success"
    if any(item.is_inductive for item in informative):
        return "inductive_invariant"
    if verifications and not informative:
        return "context_only" if all(item.candidate_contribution == "context_entailed" for item in verifications) else "solver_unknown"
    generation_metadata = generation_metadata or {}
    if not verifications and generation_metadata.get("response_echo_detected"):
        return "prompt_echo"
    # Refuted induction is decisive even when a different obligation times out.
    if verifications and all(item.assessment in {"refuted", "invalid"} for item in verifications):
        return "invalid_candidate" if all(item.assessment == "invalid" for item in verifications) else "logical_failure"
    if generation_metadata.get("done_reason") == "length" or generation_metadata.get("finish_reason") == "MAX_TOKENS":
        return "output_truncated"
    if raw.startswith("MODEL_ERROR:"):
        return "generation_aborted" if generation_metadata.get("error_kind") == "generation_repetition" else "provider_error"
    if not verifications:
        return "malformed_output"
    statuses = [status for item in verifications for status in (item.establishment, item.preservation, item.postcondition)]
    if "unknown" in statuses:
        return "solver_unknown"
    if "unsupported" in statuses:
        return "checker_limitation"
    if "invalid" in statuses:
        return "invalid_candidate"
    if "error" in statuses or "infeasible" in statuses:
        return "verification_error"
    return "logical_failure"


def feedback_prompt(base, candidates, verifications, mode):
    if mode == "trace-only":
        # Source-derived counterexamples would introduce a fourth input treatment.
        raise ValueError("Counterexample repair is not part of the trace-only baseline.")
    feedback = [{"candidate": candidate, "establishment": result.establishment,
                 "preservation": result.preservation, "postcondition": result.postcondition,
                 "counterexamples": result.counterexamples, "error": result.error}
                for candidate, result in zip(candidates, verifications)]
    return base + "\nPrevious hypothesis check feedback:\n" + json.dumps(feedback) + (
        "\nA failed establishment means the formula excludes a permitted initial loop-head state. "
        "A failed preservation means a permitted state satisfying the formula and guard can leave the formula after an update. "
        "A failed postcondition check means the formula is too weak to imply the goal at exit. "
        "Counterexamples to preservation/postcondition need not be reachable from the concrete trace: the proposed formula must exclude such states or cover their updates. "
        "Revise the complete formula, joining necessary clauses with and. Do not repeat a refuted candidate unchanged. "
        "Keep the required CANDIDATES/END format."
    )


def write_summary(path, trials):
    records = summarize(trials)
    if not records:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def source_manifest():
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for base in (ROOT / "backend", ROOT / "presentation", ROOT / "scripts", ROOT / "examples")
            for path in sorted(base.rglob("*.py"))} | {
                name: digest((ROOT / name).read_text(encoding="utf-8")) for name in ("app.py", "bootstrap.py")}


def model_artifacts(adapter, models):
    if not any(adapter.provider(model) == "ollama" for model in models):
        return {}
    try:
        with urllib.request.urlopen(f"{adapter.ollama.base_url}/api/tags", timeout=5) as response:
            tags = json.load(response)
        return {item["name"]: {key: item.get(key) for key in ("digest", "size", "details")}
                for item in tags.get("models", []) if item["name"] in models}
    except Exception:
        return {"error": "Model artifact metadata unavailable; do not assume model identity is pinned."}


def reserve_run(directory):
    directory.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        run_id = f"run_{index:03d}"
        path = directory / f"{run_id}_metadata.json"
        if (directory / f"{run_id}.csv").exists():
            index += 1
            continue
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write("{}")
            return run_id, path
        except FileExistsError:
            index += 1


def main():
    args = parse_args()
    if args.suite in {"examples", "all"} and (args.generation_strategy != "direct" or args.candidate_policy != "independent"):
        raise SystemExit("Textbook suites currently support direct generation with independent complete candidates only.")
    if args.generation_strategy == "relations_only" and args.max_repairs:
        raise SystemExit("relations_only is a discovery-only treatment; use --max-repairs 0.")
    if args.max_repairs and "trace-only" in args.input_modes:
        raise SystemExit("Use --max-repairs 0 for the three-mode baseline; evaluate counterexample repair separately without trace-only.")
    adapter = ModelAdapter()
    models = args.models or adapter.list_models()[:1]
    if not models:
        raise SystemExit("No models available; configure Ollama or server-side Gemini credentials.")
    cases = build_cases(args)
    jobs = [(case["case_id"], model, mode, repetition) for case in cases for model in models
            for mode in args.input_modes for repetition in range(1, args.repeats + 1)]
    rng = random.Random(args.seed)
    if args.schedule == "incremental":
        jobs = []
        for model in models:
            groups = defaultdict(list)
            for case in cases:
                groups[(case["example_template"], case["bound_mode"])].append(case)
            for group in groups.values():
                for case in sorted(group, key=lambda item: int(item["bound_value"] or 0)):
                    block = [(case["case_id"], model, mode, repetition) for mode in args.input_modes
                             for repetition in range(1, args.repeats + 1)]
                    rng.shuffle(block)
                    jobs.extend(block)
    elif args.schedule == "model-blocked":
        order = list(models)
        rng.shuffle(order)
        jobs = []
        for model in order:
            block = [(case["case_id"], model, mode, repetition) for case in cases
                     for mode in args.input_modes for repetition in range(1, args.repeats + 1)]
            rng.shuffle(block)
            jobs.extend(block)
    else:
        rng.shuffle(jobs)
    checker = Z3InvariantChecker(timeout_ms=args.solver_timeout_ms)
    prepared = prepare_cases(cases, checker, args)
    initial_calls = 2 if args.generation_strategy == "relations_first" else 1
    print(f"Preflight passed. Cases={len(cases)}, trials={len(jobs)}, maximum calls={len(jobs) * (initial_calls + args.max_repairs)}", flush=True)
    if args.dry_run:
        print("Dry run: no model calls or output files created.")
        return
    directory = Path(args.output_dir)
    run_id, metadata_path = reserve_run(directory)
    stem = directory / run_id
    trace_dir = directory / f"{run_id}_traces"
    trace_dir.mkdir()
    for case_id, data in prepared.items():
        data["trace_file"] = str(trace_dir / f"{case_id}.json")
        Path(data["trace_file"]).write_text(json.dumps({
            "case": data["case"], "trace": data["trace"].rows, "trace_complete": data["trace_complete"],
            "error": data["error"], "reference_verification": data.get("reference"),
        }, indent=2), encoding="utf-8")
    metadata = {
        "protocol_version": 3, "run_id": run_id, "status": "running", "arguments": vars(args),
        "generation_policy": "Textbook: minimal target-only prompt, five candidates, no supplied invariant bounds, source hints or solved examples. Scalar: three candidates. Exact END-line stopping; no automatic retries or parameter changes. Do not pool with prior generation contracts.",
        "models": models, "model_artifacts": model_artifacts(adapter, models),
        "runtime": {"python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
                    "dependencies": {name: importlib.metadata.version(name) for name in ("streamlit", "pandas", "z3-solver")}},
        "source_sha256": source_manifest(), "trial_order": jobs,
        "planned_trials": len(jobs), "completed_trials": 0,
        "specification_policy": "Identical pre/postconditions in every mode; textbook prompts contain no extra proof context, supplied bounds or solved examples. Scoring frames unchanged precondition facts only. Reference preflight is separate from model scoring.",
        "sampling_policy": "Textbook: every captured outer loop-head state is sent. Scalar: first 8 and last 5 when over 13. Full traces saved. Syntax-only has no traces and its prompt stays identical across input-size treatments; do not interpret those repetitions as a size effect.",
        "seed_policy": "Same base seed + repetition - 1 across models/modes/bounds; provider reproducibility is best effort.",
        "termination_claim": "Partial correctness only; trace completion is reported separately.",
        "verification_policy": "Integer/array Hoare obligations with access safety, element writes, certified bounded unit-step inner scans and natural integer powers. Power SAT abstractions require exact witness validation before refutation. textbook_context_v2 frames lengths across element writes. Partial correctness only; unsupported syntax and solver unknown remain explicit. Compare source hashes before pooling runs.",
        "assessment_schema_version": 2,
        "textbook_context_policy": "textbook_unassisted_v1: candidate plus unchanged input-precondition facts only; no source-derived bounds. Reference preflight uses separately labelled textbook_context_v2, never model scoring. Minimal target-only prompts; no solved test examples. Do not pool with context-assisted runs.",
        "outcome_policy": "Inductive invariants require establishment and preservation, independently of postcondition sufficiency. Legacy success remains the full-proof metric. No textual similarity or partial-obligation count is a correctness percentage.",
    }
    trials = []
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Run={run_id}; output={directory}", flush=True)
    try:
        with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as csv_file, \
             stem.with_suffix(".jsonl").open("w", encoding="utf-8") as jsonl_file, \
             (directory / f"{run_id}_trials.jsonl").open("w", encoding="utf-8") as trial_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
            writer.writeheader()
            csv_file.flush()
            for position, (case_id, model, mode, repetition) in enumerate(jobs, 1):
                data = prepared[case_id]
                case = data["case"]
                trial_id = digest(json.dumps([case_id, model, mode, repetition]))[:20]
                trial = {**{key: case[key] for key in ("example_name", "example_template", "bound_mode", "bound_value")},
                         "trial_id": trial_id, "model_name": model, "input_mode": mode,
                         "prompt_style": args.prompt_style, "repetition": repetition,
                         "generation_strategy": args.generation_strategy,
                         "candidate_policy": args.candidate_policy, "raw_model_success": False,
                         "verification_contract": case["verification_contract"],
                         "generation_contract": case["generation_contract"], "candidate_limit": case["candidate_limit"],
                         "success": False, "first_success": False, "status": "unsupported",
                         "generation_seconds": 0, "first_output_hash": "", "calls": 0}
                trial.update(candidate_generated=False, inductive_found=False,
                             first_inductive_found=False, sufficient_found=False,
                             assessment_schema_version=2)
                discovery_only = data.get("discovery_only", False)
                trial.update(scoring_eligible=not bool(data["error"]), unscored_reason=data["error"])
                base = build_prompt(data, mode, args) if not data["error"] or discovery_only else ""
                prompt = base
                for repair in range(1 + args.max_repairs):
                    seed = args.seed + repetition - 1 + repair
                    started = time.perf_counter()
                    relation_prompt = PromptBuilder().build_relation_prompt(
                        mode, data["syntax"], data["trace"], case["precondition"]
                    ) if base and repair == 0 and args.generation_strategy in {"relations_first", "relations_only"} else ""
                    generated = generate_hypotheses(
                        adapter, model, prompt, relation_prompt,
                        relations_only=args.generation_strategy == "relations_only",
                        timeout_seconds=args.timeout_seconds, temperature=args.temperature, seed=seed,
                        max_output_tokens=args.max_output_tokens, context_length=args.context_length,
                        repeat_penalty=args.repeat_penalty,
                    ) if base else None
                    raw = generated.output if generated else ""
                    if generated:
                        prompt = generated.prompt
                        if repair == 0:
                            base = prompt
                    duration = time.perf_counter() - started if base else 0
                    checked_at = time.perf_counter()
                    if base and data.get("textbook"):
                        from backend.infrastructure.candidate_parser import extract_book_candidates
                        verifications, origins = verify_candidates(raw, extract_book_candidates,
                            lambda candidate: verify_textbook_case(case, candidate, args.solver_timeout_ms),
                            prompt=generated.prompt, candidate_limit=case["candidate_limit"])
                    else:
                        verifications, origins = verify_hypotheses(
                            adapter, raw, checker, data["parsed"], case["precondition"], case["postcondition"], args.candidate_policy, prompt=generated.prompt,
                        ) if base else ([], {})
                    candidates = [result.candidate for result in verifications]
                    for result in verifications:
                        concrete_holds = (all(evaluate_textbook(result.candidate, row) for row in data["trace"].rows)
                                          if data.get("textbook") and result.is_inductive else
                                          trace_holds(result.candidate, data["trace"]) if result.is_inductive else True)
                        if result.is_inductive and not concrete_holds:
                            raise RuntimeError("Formal proof disagrees with captured execution; experiment stopped.")
                    verification_seconds = time.perf_counter() - checked_at
                    generation_outcome = failure_category(raw, verifications, generated.metadata) if base else "unsupported"
                    category = "unsupported" if discovery_only else generation_outcome
                    trial["generation_outcome"] = generation_outcome
                    trial["checker_limited"] = discovery_only or has_unresolved_checker_work(verifications)
                    trial["calls"] += generated.calls if generated else 0
                    trial["generation_seconds"] += duration
                    trial["status"] = category
                    trial["success"] = category == "success"
                    assessed = assess_candidates(verifications)
                    if discovery_only:
                        assessed = {**assessed, "inductive_found": False, "sufficient_found": False}
                    trial["candidate_generated"] |= assessed["candidate_generated"]
                    trial["inductive_found"] |= assessed["inductive_found"]
                    trial["sufficient_found"] |= assessed["sufficient_found"]
                    trial["raw_model_success"] |= not discovery_only and any(result.accepted and result.has_model_contribution and origins[result.candidate] == "model_output" for result in verifications)
                    if repair == 0:
                        trial["first_success"] = trial["success"]
                        trial["first_inductive_found"] = assessed["inductive_found"]
                        trial["first_output_hash"] = digest(raw)
                    attempt_id = f"{trial_id}-{repair}"
                    common = {
                        **{key: case.get(key, "") for key in FIELDS if key in case},
                        "run_id": run_id, "trial_id": trial_id, "attempt_id": attempt_id,
                        "protocol_version": 3, "repetition": repetition, "repair_index": repair, "seed": seed,
                        "model_name": model, "provider": adapter.provider(model), "input_mode": mode,
                        "prompt_style": args.prompt_style, "temperature": args.temperature,
                        "verification_backend": "z3" if base and not discovery_only else "unsupported_by_current_z3_subset",
                        "proof_context": json.dumps(case["proof_context"]),
                        "trace_complete": data["trace_complete"], "trace_rows": len(data["trace"].rows),
                        "trace_file": data["trace_file"], "trace_iteration_limit": args.trace_cap,
                        "prompt": prompt, "prompt_sha256": digest(prompt), "prompt_refresh_id": "",
                        "raw_output": raw, "failure_category": category, "duration_seconds": duration,
                        "verification_seconds": verification_seconds,
                        "generation_strategy": args.generation_strategy,
                        "candidate_policy": args.candidate_policy, "candidate_origins": json.dumps(origins),
                        "model_calls": generated.calls if generated else 0,
                        "generation_metadata": json.dumps(generated.metadata if generated else {}),
                    }
                    record = {**common, "assessment_schema_version": 2,
                              "scoring_eligible": trial["scoring_eligible"], "unscored_reason": data["error"],
                              "generation_outcome": generation_outcome,
                              "verifications": [item.to_record() for item in verifications]}
                    jsonl_file.write(json.dumps(record) + "\n")
                    jsonl_file.flush()
                    for index, result in enumerate(verifications or [None], 1):
                        statuses = (result.establishment, result.preservation, result.postcondition) if result else ("not checked",) * 3
                        row = {**common, "candidate_index": index if result else 0,
                               "candidate_origin": origins.get(result.candidate, "") if result else "",
                               "candidate": result.candidate if result else "", "parseable": "yes" if result else "no",
                               "accepted": "unscored" if discovery_only else (("yes" if result.accepted else "no") if result else ("unscored" if not base else "no")),
                               "establishment": statuses[0], "preservation": statuses[1], "postcondition_result": statuses[2],
                               "counterexamples": json.dumps(result.counterexamples if result else {}),
                               "error": result.error if result else (data["error"] or (raw if category == "provider_error" else category))}
                        row.update(assessment=result.assessment if result else "not_checked",
                                   effective_candidate=result.effective_candidate if result else "",
                                   outcome_kind=result.outcome_kind if result else "not_established",
                                   limitation_scope=result.limitation_scope if result else "",
                                   candidate_contribution=result.candidate_contribution if result else "",
                                   is_inductive=("yes" if result.is_inductive else "no") if result else "",
                                   proves_postcondition=("yes" if result.proves_postcondition else "no") if result else "",
                                   obligations_proved=result.obligations_proved if result else "")
                        writer.writerow(row)
                    csv_file.flush()
                    print(f"[{position}/{len(jobs)}] {case['example_name']} | {model} | {mode} | repeat={repetition} repair={repair} => {category} ({duration:.1f}s)", flush=True)
                    if discovery_only or trial["success"] or not base or category in ("provider_error", "generation_aborted", "checker_limitation", "solver_unknown", "verification_error"):
                        break
                    prompt = feedback_prompt(base, candidates, verifications, mode) if repair < args.max_repairs else base
                trial_file.write(json.dumps(trial) + "\n")
                trial_file.flush()
                trials.append(trial)
                write_summary(directory / f"{run_id}_summary.csv", trials)
                metadata["completed_trials"] = len(trials)
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        metadata["status"] = "complete"
    except BaseException as exc:
        metadata["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        metadata["error"] = type(exc).__name__
        raise
    finally:
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Completed {len(trials)} trials. Summary: {run_id}_summary.csv", flush=True)


if __name__ == "__main__":
    main()
