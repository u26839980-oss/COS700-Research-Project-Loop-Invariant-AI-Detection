"""Ordered assessment policy, independent of solvers and presentation."""
from enum import StrEnum


class HypothesisAssessment(StrEnum):
    SUFFICIENT = "sufficient"
    INDUCTIVE = "inductive"
    REFUTED = "refuted"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    INFEASIBLE = "infeasible"
    ERROR = "error"
    NOT_CHECKED = "not_checked"


def assess_obligations(establishment: str, preservation: str,
                       postcondition: str) -> HypothesisAssessment:
    # Pattern order encodes policy precedence; goal status never erases induction.
    match establishment, preservation, postcondition:
        case "proved", "proved", "proved":
            return HypothesisAssessment.SUFFICIENT
        case "proved", "proved", _:
            return HypothesisAssessment.INDUCTIVE
        case ("failed", _, _) | (_, "failed", _):
            return HypothesisAssessment.REFUTED
        case ("invalid", _, _) | (_, "invalid", _):
            return HypothesisAssessment.INVALID
        case ("unsupported", _, _) | (_, "unsupported", _):
            return HypothesisAssessment.UNSUPPORTED
        case ("unknown", _, _) | (_, "unknown", _):
            return HypothesisAssessment.UNKNOWN
        case ("infeasible", _, _) | (_, "infeasible", _):
            return HypothesisAssessment.INFEASIBLE
        case ("error", _, _) | (_, "error", _):
            return HypothesisAssessment.ERROR
        case _:
            return HypothesisAssessment.NOT_CHECKED
