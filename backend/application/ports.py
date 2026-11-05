"""Small interfaces for the dependencies used by the analysis workflows."""
from __future__ import annotations

from typing import Any, Protocol

from backend.domain.models import ExecutionTrace, VerificationResult


class ModelPort(Protocol):
    last_generation_metadata: dict[str, Any]

    def generate(self, model_name: str, prompt: str, **settings: Any) -> str: ...
    def extract_candidates(self, raw_output: str) -> list[str]: ...
    def provider(self, model_name: str) -> str: ...


class RepositoryPort(Protocol):
    def append(self, payload: dict[str, Any]) -> str: ...


class ScalarCheckerPort(Protocol):
    def verify(self, parsed: Any, precondition: str, invariant: str,
               postcondition: str) -> VerificationResult: ...


class TextbookCheckerPort(Protocol):
    def verify(self, example_name: str, candidate: str, **specification: Any) -> VerificationResult: ...


class TracePort(Protocol):
    def execute(self, parsed: Any, initial_environment: dict[str, int],
                iteration_limit: int) -> ExecutionTrace: ...


class PatternPort(Protocol):
    def generate(self, parsed: Any, trace: ExecutionTrace) -> list[str]: ...
    def explain(self, candidate: str) -> str: ...


class PromptPort(Protocol):
    def build(self, **target: Any) -> str: ...
    def build_relation_prompt(self, mode: str, syntax_features: dict, trace: ExecutionTrace,
                              precondition: str) -> str: ...
