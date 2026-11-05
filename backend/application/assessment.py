"""Research outcomes: inductiveness and goal sufficiency are separate facts."""
from collections.abc import Sequence

from backend.domain.models import VerificationResult


def has_unresolved_checker_work(results: Sequence[VerificationResult]) -> bool:
    if any(result.proves_postcondition and result.has_model_contribution for result in results):
        return False
    return any(result.candidate_contribution == "unknown" or (
        result.assessment not in {"refuted", "invalid"} and
        any(status in {"unknown", "unsupported", "error"} for status in
            (result.establishment, result.preservation, result.postcondition))) for result in results)


def assess_candidates(results: Sequence[VerificationResult]) -> dict[str, bool | int]:
    informative = [result for result in results if result.has_model_contribution]
    return {
        "candidate_generated": bool(results),
        "inductive_found": any(result.is_inductive for result in informative),
        "sufficient_found": any(result.proves_postcondition for result in informative),
        "candidate_count": len(results),
        "inductive_candidate_count": sum(result.is_inductive for result in informative),
        "sufficient_candidate_count": sum(result.proves_postcondition for result in informative),
    }
