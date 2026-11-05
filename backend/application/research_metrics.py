import json
import math
from collections import Counter, defaultdict


CHECKER_STATUSES = {"unsupported", "unsupported_candidate", "checker_limitation", "solver_unknown", "verification_error"}
MODEL_FAILURE_STATUSES = {"logical_failure", "invalid_candidate", "malformed_output", "prompt_echo", "output_truncated"}


def wilson(successes, total):
    if not total:
        return "", ""
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0, center - radius), min(1, center + radius)


def summarize(trials):
    groups = defaultdict(list)
    for trial in trials:
        groups[tuple(trial[key] for key in ("example_name", "example_template", "bound_mode", "bound_value",
                                            "model_name", "input_mode", "prompt_style")) +
               (trial.get("generation_strategy", "direct"), trial.get("candidate_policy", "independent"),
                trial.get("verification_contract", "standalone_v1"),
                trial.get("generation_contract", "legacy_candidates_v1"), trial.get("candidate_limit", 3))].append(trial)
    summaries = []
    for key, group in groups.items():
        eligible = [trial for trial in group if trial["status"] != "unsupported"]
        successes = sum(trial["success"] for trial in eligible)
        lower, upper = wilson(successes, len(eligible))
        record = dict(zip(("example_name", "example_template", "bound_mode", "bound_value", "model_name",
                           "input_mode", "prompt_style", "generation_strategy", "candidate_policy", "verification_contract",
                           "generation_contract", "candidate_limit"), key))
        record.update(trials=len(eligible), unsupported_trials=len(group) - len(eligible), successes=successes,
                      first_attempt_successes=sum(trial["first_success"] for trial in eligible),
                      raw_model_successes=sum(trial.get("raw_model_success", trial["success"]) for trial in eligible),
                      success_rate=successes / len(eligible) if eligible else "",
                      ci95_low=lower, ci95_high=upper,
                      unique_outputs=len({trial["first_output_hash"] for trial in eligible}),
                      mean_generation_seconds=sum(trial["generation_seconds"] for trial in eligible) / len(eligible) if eligible else "",
                      total_model_calls=sum(trial.get("calls", 1) for trial in eligible),
                      non_success_counts=json.dumps(dict(Counter(trial["status"] for trial in eligible if not trial["success"]))),
                      failure_counts=json.dumps(dict(Counter(trial["status"] for trial in eligible
                          if trial["status"] in MODEL_FAILURE_STATUSES))))
        assessed = [trial for trial in eligible if "inductive_found" in trial]
        inductive = sum(trial["inductive_found"] for trial in assessed)
        low, high = wilson(inductive, len(assessed))
        record.update(inductive_assessed_trials=len(assessed), inductive_trials=inductive,
                      inductive_rate=inductive / len(assessed) if assessed else "",
                      inductive_ci95_low=low, inductive_ci95_high=high,
                      first_inductive_trials=sum(trial.get("first_inductive_found", False) for trial in assessed),
                      generated_candidate_trials=sum(trial.get("candidate_generated", False) for trial in assessed),
                      full_proof_trials=successes, full_proof_rate=record["success_rate"])
        summaries.append(record)
        record.update(
            checker_limited_trials=sum(trial.get("checker_limited", trial["status"] in CHECKER_STATUSES) for trial in group),
            candidate_refuted_trials=sum(trial["status"] == "logical_failure" for trial in group),
            invalid_output_trials=sum(trial["status"] in {"invalid_candidate", "malformed_output", "prompt_echo"} for trial in group),
            inconclusive_trials=sum(trial["status"] == "solver_unknown" for trial in group),
            checker_outcome_counts=json.dumps(dict(Counter(trial["status"] for trial in group if trial["status"] in CHECKER_STATUSES))),
            model_failure_counts=json.dumps(dict(Counter(trial["status"] for trial in group if trial["status"] in MODEL_FAILURE_STATUSES))),
            generation_failure_counts=json.dumps(dict(Counter(trial["status"] for trial in group
                if trial["status"] in {"generation_aborted", "provider_error", "output_truncated"}))),
        )
    return summaries
