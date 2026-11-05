"""UI wording for domain outcomes; never used to decide correctness."""
from backend.domain.models import VerificationResult
from backend.domain.verification_policy import HypothesisAssessment


def assessment_label(result: VerificationResult) -> str:
    if result.outcome_kind == "contribution_inconclusive":
        return "Joint invariant proved; model contribution undetermined"
    if result.outcome_kind == "context_only":
        return "Supplied context only; no additional model invariant"
    match result.assessment:
        case HypothesisAssessment.SUFFICIENT:
            return "Inductive; proves postcondition"
        case HypothesisAssessment.INDUCTIVE:
            match result.postcondition:
                case "failed":
                    return "Inductive but insufficient"
                case _:
                    return "Inductive; postcondition not established"
        case HypothesisAssessment.REFUTED:
            return "Candidate refuted"
        case HypothesisAssessment.INVALID:
            return "Invalid candidate syntax or variables"
        case HypothesisAssessment.UNSUPPORTED:
            return "Checker limitation; unscored"
        case HypothesisAssessment.UNKNOWN:
            return "Solver inconclusive; not refuted"
        case HypothesisAssessment.INFEASIBLE:
            return "Infeasible precondition"
        case HypothesisAssessment.ERROR:
            return "Checker error"
        case HypothesisAssessment.NOT_CHECKED:
            return "Not checked"
    raise ValueError(f"No display label for assessment {result.assessment!r}.")
