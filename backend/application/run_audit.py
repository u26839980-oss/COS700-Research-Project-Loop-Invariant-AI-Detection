from collections import Counter, defaultdict
from backend.domain.models import VerificationResult
from backend.application.assessment import assess_candidates


def audit(attempts, trials):
    groups = defaultdict(list)
    for attempt in attempts:
        groups[attempt["trial_id"]].append(attempt)
    counts = Counter()
    inductive_trials = 0
    generated_trials = 0
    sufficient_trials = 0
    evidence = []
    for trial in trials:
        records = groups[trial["trial_id"]]
        verifications = [item for record in records for item in record["verifications"]]
        assessed = [VerificationResult.from_record(item) for item in verifications]
        summary = assess_candidates(assessed)
        inductive_trials += summary["inductive_found"]
        generated_trials += bool(assessed)
        sufficient_trials += summary["sufficient_found"]
        insufficient = [item for item in assessed if item.inductive_but_insufficient and item.has_model_contribution]
        if trial["success"]:
            diagnosis = "sufficient_invariant_found"
        elif insufficient:
            diagnosis = "inductive_but_insufficient"
        elif summary["inductive_found"]:
            diagnosis = "inductive_goal_undetermined"
        else:
            diagnosis = trial["status"]
        counts[diagnosis] += 1
        evidence.append({
            "trial_id": trial["trial_id"], "example": trial["example_name"],
            "input_mode": trial["input_mode"], "saved_status": trial["status"],
            "diagnosis": diagnosis,
            "inductive_but_insufficient_candidates": [item.candidate for item in insufficient],
            "verification_contract": trial.get("verification_contract", "standalone_v1"),
            "attempt_ids": [record["attempt_id"] for record in records],
            "candidate_assessments": [{"candidate": item.candidate, "assessment": item.assessment,
                "outcome_kind": item.outcome_kind, "candidate_contribution": item.candidate_contribution,
                "obligations_proved": item.obligations_proved} for item in assessed],
        })
    return {"completed_trials": len(trials), "successful_trials": sum(bool(t["success"]) for t in trials),
            "assessment_schema_version": 1, "generated_candidate_trials": generated_trials,
            "inductive_trials": inductive_trials, "full_proof_trials_from_obligations": sufficient_trials,
            "diagnoses": dict(counts), "trials": evidence,
            "interpretation": "Inductiveness requires establishment and preservation. Success additionally requires the postcondition obligation. Historical statuses are preserved; diagnoses are descriptive, not new scores."}
