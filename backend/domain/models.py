from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from backend.domain.verification_policy import HypothesisAssessment, assess_obligations


@dataclass(frozen=True)
class ExecutionTrace:
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class VerificationResult:
    candidate: str
    accepted: bool
    establishment: str
    preservation: str
    postcondition: str
    error: str = ""
    reason: str = ""
    hoare_steps: list[str] = field(default_factory=list)
    counterexamples: dict[str, dict[str, str]] = field(default_factory=dict)
    verification_contract: str = "standalone_v1"
    proof_context: dict[str, Any] = field(default_factory=dict)
    effective_candidate: str = ""
    limitation_scope: str = ""
    candidate_contribution: str = "not_assessed"

    @property
    def has_model_contribution(self) -> bool:
        return self.candidate_contribution in {"not_assessed", "nontrivial"}

    @property
    def outcome_kind(self) -> str:
        if self.is_inductive and self.candidate_contribution == "unknown":
            return "contribution_inconclusive"
        if self.is_inductive and not self.has_model_contribution:
            return "context_only"
        match self.assessment:
            case "sufficient":
                return "proved"
            case "inductive" if self.postcondition == "failed":
                return "insufficient_for_goal"
            case "refuted":
                return "candidate_refuted"
            case "invalid":
                return "invalid_candidate"
            case "infeasible":
                return "benchmark_infeasible"
            case "error":
                return "checker_error"
        if "unsupported" in (self.establishment, self.preservation, self.postcondition):
            return "checker_limitation"
        if "unknown" in (self.establishment, self.preservation, self.postcondition):
            return "solver_inconclusive"
        return "not_established"

    @property
    def is_inductive(self) -> bool:
        return self.establishment == "proved" and self.preservation == "proved"

    @property
    def proves_postcondition(self) -> bool:
        return self.is_inductive and self.postcondition == "proved"

    @property
    def obligations_proved(self) -> int:
        return sum(status == "proved" for status in (
            self.establishment, self.preservation, self.postcondition))

    @property
    def assessment(self) -> HypothesisAssessment:
        return assess_obligations(self.establishment, self.preservation, self.postcondition)

    @property
    def inductive_but_insufficient(self) -> bool:
        return (self.establishment == "proved" and self.preservation == "proved"
                and self.postcondition == "failed")

    def to_row(self) -> dict[str, str]:
        return {
            "candidate": self.candidate,
            "accepted": "yes" if self.accepted else "no",
            "establishment": self.establishment,
            "preservation": self.preservation,
            "postcondition": self.postcondition,
            "reason": self.reason,
            "error": self.error,
            "assessment": self.assessment,
            "is_inductive": "yes" if self.is_inductive else "no",
            "proves_postcondition": "yes" if self.proves_postcondition else "no",
            "obligations_proved": str(self.obligations_proved),
            "outcome_kind": self.outcome_kind,
            "verification_contract": self.verification_contract,
            "limitation_scope": self.limitation_scope,
            "candidate_contribution": self.candidate_contribution,
        }

    def to_record(self) -> dict[str, Any]:
        from dataclasses import asdict
        return {**asdict(self), "assessment": self.assessment,
                "is_inductive": self.is_inductive, "proves_postcondition": self.proves_postcondition,
                "obligations_proved": self.obligations_proved, "outcome_kind": self.outcome_kind}

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> VerificationResult:
        from dataclasses import fields
        values = {item.name: record[item.name] for item in fields(cls) if item.name in record}
        values.setdefault("accepted", all(record.get(key) == "proved" for key in (
            "establishment", "preservation", "postcondition")))
        return cls(**values)


@dataclass(frozen=True)
class ModelCandidateResult:
    model_name: str
    mode: str
    raw_output: str
    verifications: list[VerificationResult]
    prompt: str = ""
    provider: str = "ollama"
    generation_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisResult:
    trace: ExecutionTrace
    syntax_features: dict[str, Any]
    user_verification: VerificationResult
    model_results: list[ModelCandidateResult] = field(default_factory=list)
    log_path: str = ""
    initial_environment: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ReverseConstructionResult:
    invariant: str
    precondition: str
    postcondition: str
    guard: str
    program_text: str
    construction_steps: list[str]
    verification: VerificationResult
    trace: ExecutionTrace
