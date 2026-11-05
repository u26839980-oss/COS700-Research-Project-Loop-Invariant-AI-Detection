from __future__ import annotations

from collections.abc import Callable
from backend.domain.models import VerificationResult
from backend.application.response_validation import candidate_response
from backend.domain.candidate_contract import SCALAR_CANDIDATE_LIMIT, validate_candidate_limit


def verify_candidates(output: str, extract_candidates: Callable[[str], list[str]],
                      verify_candidate: Callable[[str], VerificationResult],
                      candidate_policy: str = "independent", *, prompt: str = "",
                      candidate_limit: int = SCALAR_CANDIDATE_LIMIT) -> tuple[list[VerificationResult], dict[str, str]]:
    if candidate_policy not in {"independent", "conjoin_inductive"}:
        raise ValueError(f"Unknown candidate policy: {candidate_policy}")
    validate_candidate_limit(candidate_limit)
    response, _ = candidate_response(output, prompt)
    candidates = extract_candidates(response)[:candidate_limit]
    origins = {candidate: "model_output" for candidate in candidates}
    results = [verify_candidate(candidate) for candidate in candidates]
    if candidate_policy == "conjoin_inductive":
        # Established, individually preserved clauses can be conjoined soundly.
        clauses = [result.candidate for result in results
                   if result.is_inductive]
        if len(clauses) > 1:
            combined = " and ".join(f"({clause})" for clause in clauses)
            if combined not in origins:
                origins[combined] = "composed_inductive_clauses"
                results.append(verify_candidate(combined))
    return results, origins


def verify_hypotheses(adapter, output, checker, parsed, precondition, postcondition,
                      candidate_policy="independent", *, prompt=""):
    return verify_candidates(output, adapter.extract_candidates,
        lambda candidate: checker.verify(parsed, precondition, candidate, postcondition), candidate_policy, prompt=prompt)
