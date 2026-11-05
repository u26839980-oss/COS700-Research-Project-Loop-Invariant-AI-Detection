"""No-execution research planning; tasks, repetitions and evidence budgets differ."""
from __future__ import annotations

import math
from statistics import NormalDist


def make_plan(tasks, models, *, repeats=5, modes=("syntax-only",), trace_budgets=(5, 10, 15, 20, 25),
              timeout_seconds=300, minimum_effect=0.10, discordant_fraction=0.30):
    tasks = list(dict.fromkeys(tasks))
    models = list(dict.fromkeys(model.strip() for model in models if model.strip()))
    modes = list(dict.fromkeys(modes))
    budgets = list(dict.fromkeys(trace_budgets))
    if not tasks or not models or not modes or repeats < 1 or timeout_seconds < 1:
        raise ValueError("Select tasks, models, modes and positive resource limits.")
    if set(modes) - {"syntax-only", "trace-only", "syntax-plus-trace"}:
        raise ValueError("Unknown input mode")
    if not 0 < minimum_effect < 1 or not minimum_effect <= discordant_fraction <= 1:
        raise ValueError("Power assumptions need 0 < effect <= discordance <= 1.")
    if any(type(value) is not int or value < 1 for value in budgets) or not budgets:
        raise ValueError("Evidence budgets must be positive integers")
    treatments = sum(1 if mode == "syntax-only" else len(budgets) for mode in modes)
    calls = len(tasks) * len(models) * repeats * treatments
    # Planning approximation for paired binary outcomes, not an achieved-power claim.
    normal = NormalDist()
    zsum = normal.inv_cdf(0.975) + normal.inv_cdf(0.8)
    pairs = math.ceil(zsum * zsum * discordant_fraction / (minimum_effect * minimum_effect))
    return dict(kind="experiment_plan_only", model_calls=0, planned_generation_calls=calls,
        distinct_tasks=len(tasks), tasks=tasks, models=models, repeats=repeats, input_modes=modes,
        trace_observation_budgets=budgets, syntax_budget_policy="One syntax-only treatment, not repeated for each trace budget.",
        seeds=list(range(repeats)), temperature_policy="Preregister temperature; temperature-zero repetitions may be identical.",
        worst_case_generation_hours=calls * timeout_seconds / 3600,
        runtime_caveat="Timeout ceiling only; excludes checker time, loading, queues and retries. Measure a pilot before estimating actual duration.",
        power_planning=dict(alpha_two_sided=0.05, target_power=0.80, minimum_absolute_effect=minimum_effect,
            assumed_paired_discordance=discordant_fraction, approximate_required_distinct_task_pairs=pairs,
            caveat="Approximation for one prespecified paired comparison and independent task pairs. Estimate discordance from a pilot; adjust for multiple comparisons and related families."),
        run_ready=False, blockers=["Audit and freeze the source trace options, input profiles and coverage eligibility before batch execution.",
            "No batch execution is authorized by this plan. Freeze model/checker hashes and validate eligibility first."],
        analysis_policy=["Use paired comparisons on the same tasks; report cluster-by-program confidence intervals.",
            "Keep unsafe controls separate from invariant discovery; do not silently drop hard or unsupported tasks.",
            "Report parsing, candidate refutation, insufficient goal strength, native safety failures, resource limits and checker errors separately.",
            "Repeated seeds measure within-task consistency, not additional independent programs.",
            "Keep held-out tasks separate from prompt development; do not tune on final evaluation outputs."])
