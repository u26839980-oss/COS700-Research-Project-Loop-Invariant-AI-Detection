"""Separate generation, proof and benchmark-profile outcomes in source pilots."""
from collections import Counter


UNSAFE = {"code2inv-026", "code2inv-027", "code2inv-072", "svcomp-loops-array-2"}


def benchmark_role(task_id):
    if task_id in UNSAFE:
        return "unsafe_control"
    if task_id == "svcomp-loop-lit-css2003":
        return "native_safety_profile_mismatch"
    return "positive"


def classify(record):
    """Classify evidence, without treating missing proofs as refutations."""
    generation = record.get("generation", {})
    meta = generation.get("metadata", {})
    if meta.get("provider_error") or generation.get("raw_output", "").startswith("MODEL_ERROR:"):
        return "provider_or_resource_failure"
    if record["status"] in {"context_limit", "sampling_limitation", "execution_error"}:
        return record["status"]
    if record["status"] == "invalid_model_output":
        return "generation_truncated" if meta.get("done_reason") == "length" else "model_output_format_failure"
    if record["status"] == "candidate_admission_failure":
        return "candidate_language_or_schema_rejected"
    checks = record.get("verifications", [])
    if checks:
        if any(item.get("accepted") for item in checks):
            return "full_proof"
        if any(item.get("is_inductive") for item in checks):
            return "inductive_insufficient"
        kinds = {item.get("outcome_kind") for item in checks}
        if any(item.get("limitation_scope") == "candidate_expression" and
               "outside the upstream invariant signature" in item.get("error", "") for item in checks):
            if all(item.get("outcome_kind") in {"candidate_refuted", "invalid_candidate"} or
                   "outside the upstream invariant signature" in item.get("error", "") for item in checks):
                return "model_candidate_failure"
        if kinds & {"checker_limitation", "solver_inconclusive", "checker_error", "benchmark_infeasible"}:
            return "checker_limitation_or_inconclusive"
        if kinds <= {"candidate_refuted", "invalid_candidate"}:
            return "model_candidate_failure"
        return "unresolved"
    native = record.get("native_verification", {})
    status = native.get("status", record["status"])
    if status == "contracts_and_safety_proved":
        return "full_proof"
    if status == "insufficient_for_goal":
        return "inductive_insufficient" if native.get("invariant_proved") else "goal_not_proved"
    if status == "contract_refuted":
        return "model_candidate_failure"
    if status in {"native_safety_obligation_failed", "native_safety_counterexample", "safety_counterexample"}:
        return "native_safety_failure"
    if status == "safety_proved_complete_unwinding":
        return "safety_only_no_invariant_proof"
    return "checker_limitation_or_inconclusive"


def summarize(rows, planned):
    groups = {}
    for row in rows:
        key = (row["model"], row["mode"], row["budget"], row["benchmark_role"])
        groups.setdefault(key, []).append(row)
    return dict(planned=planned, completed=len(rows), pending=planned-len(rows),
        model_calls=sum(row.get("model_calls", 0) for row in rows),
        outcomes=dict(Counter(row["outcome"] for row in rows)),
        groups=[dict(model=key[0], mode=key[1], budget=key[2], benchmark_role=key[3],
                     completed=len(items), outcomes=dict(Counter(item["outcome"] for item in items)))
                for key, items in sorted(groups.items(), key=lambda item: str(item[0]))],
        caveats=["One generation per treatment: this pilot does not estimate within-task consistency.",
                 "Full proof means the declared checker semantics, not termination or unseen-language correctness.",
                 "Unsafe controls and the stronger native-safety profile mismatch are not positive proof denominators.",
                 "Trace budgets count retained observations per loop per sampled input, not program bounds.",
                 "Timeout, context, sampling and unsupported-expression cases are not established model reasoning failures."])
