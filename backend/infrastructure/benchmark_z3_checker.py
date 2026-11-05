from __future__ import annotations

from backend.domain.models import VerificationResult
from backend.domain.textbook_contract import TextbookProofContext


class BenchmarkZ3CheckError(RuntimeError):
    pass


class BenchmarkZ3Checker:
    """Proofs use the supplied program/specification, never the reference answer."""

    def __init__(self, timeout_ms: int = 5000):
        self.timeout_ms = timeout_ms

    def verify(self, example_name: str, candidate: str, expected: str = "", *,
               program: str | None = None, precondition: str = "", postcondition: str = "",
               array_names: tuple[str, ...] = ("A",),
               proof_context: TextbookProofContext | None = None) -> VerificationResult:
        if program is not None:
            from backend.infrastructure.array_z3_checker import ArrayZ3Checker
            return ArrayZ3Checker(timeout_ms=self.timeout_ms).verify_program(
                program, precondition, candidate, postcondition, array_names, proof_context=proof_context)
        matches = normalise_formula(candidate) == normalise_formula(expected)
        return VerificationResult(
            candidate=candidate, accepted=False,
            establishment="unsupported", preservation="unsupported", postcondition="unsupported",
            error=f"Formal semantics for {example_name} are not implemented; reference matching is not a proof.",
            hoare_steps=[
                f"Normalized reference match: {'yes' if matches else 'no'}.",
                "No Hoare proof was attempted for this unsupported benchmark.",
            ],
        )


def normalise_formula(formula: str) -> str:
    return formula.lower().strip().replace("**", "^").replace(" ", "").replace("`", "")
