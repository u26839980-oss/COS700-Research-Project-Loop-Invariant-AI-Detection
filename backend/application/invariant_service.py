from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from backend.domain.models import AnalysisResult, ModelCandidateResult
from backend.application.hypothesis_generation import generate_hypotheses
from backend.application.hypothesis_verification import verify_hypotheses
from backend.application.ports import ModelPort, RepositoryPort, ScalarCheckerPort, TracePort, PatternPort, PromptPort


class InvariantWorkbenchService:
    def __init__(self, *, trace_executor: TracePort, prompt_builder: PromptPort,
                 pattern_hypothesizer: PatternPort, repository: RepositoryPort,
                 checker_factory: Callable[[], ScalarCheckerPort],
                 parse_program: Callable, extract_syntax_features: Callable,
                 evaluate_precondition: Callable) -> None:
        self.trace_executor = trace_executor
        self.prompt_builder = prompt_builder
        self.pattern_hypothesizer = pattern_hypothesizer
        self.repository = repository
        self.checker_factory = checker_factory
        self.parse_program = parse_program
        self.extract_syntax_features = extract_syntax_features
        self.evaluate_precondition = evaluate_precondition

    def analyse(
        self,
        program_text: str,
        precondition: str,
        postcondition: str,
        user_invariant: str,
        iteration_limit: int,
        model_names: list[str],
        model_mode: str,
        model_adapter: ModelPort,
        guidance_examples: list[dict[str, str]] | None = None,
        frontend_prompt_addition: str = "",
        prompt_style: str = "original_few_shot",
        temperature: float = 0,
        prompt_refresh_id: str = "",
        progress_callback: Callable[[str], None] | None = None,
        generation_strategy: str = "direct",
        candidate_policy: str = "independent",
        max_output_tokens: int = 384,
        repeat_penalty: float | None = None,
        initial_environment: dict[str, int] | None = None,
    ) -> AnalysisResult:
        self._notify(progress_callback, "Parsing program and collecting trace data.")
        core_result = self.analyse_without_models(
            program_text=program_text,
            precondition=precondition,
            postcondition=postcondition,
            user_invariant=user_invariant,
            iteration_limit=iteration_limit,
            progress_callback=progress_callback,
            initial_environment=initial_environment,
        )

        parsed = self.parse_program(program_text)
        checker = self.checker_factory()
        model_results = list(core_result.model_results)

        for model_name in model_names:
            self._notify(progress_callback, f"Asking model: {model_name}.")
            prompt = self.prompt_builder.build(
                mode=model_mode,
                syntax_features=core_result.syntax_features,
                trace=core_result.trace,
                precondition=precondition,
                postcondition=postcondition,
                guidance_examples=guidance_examples,
                frontend_prompt_addition=frontend_prompt_addition,
                prompt_style=prompt_style,
                prompt_refresh_id=prompt_refresh_id,
            )
            relation_prompt = self.prompt_builder.build_relation_prompt(
                model_mode, core_result.syntax_features, core_result.trace, precondition
            ) if generation_strategy in {"relations_first", "relations_only"} else ""
            generated = generate_hypotheses(model_adapter, model_name, prompt, relation_prompt,
                                             relations_only=generation_strategy == "relations_only",
                                             temperature=temperature, max_output_tokens=max_output_tokens,
                                             repeat_penalty=repeat_penalty)
            raw_output = generated.output
            self._notify(progress_callback, f"Checking hypotheses from: {model_name}.")
            checked, origins = verify_hypotheses(model_adapter, raw_output, checker, parsed,
                                                 precondition, postcondition, candidate_policy, prompt=generated.prompt)
            verifications = [
                self._with_reason(
                    result,
                    self._ai_reason(result.candidate, model_name, model_mode) + f" Origin: {origins[result.candidate]}.",
                )
                for result in checked
            ]
            generated.metadata["candidate_origins"] = origins
            model_results.append(
                ModelCandidateResult(
                    model_name=model_name,
                    mode=model_mode,
                    raw_output=raw_output,
                    verifications=verifications,
                    prompt=generated.prompt,
                    provider=model_adapter.provider(model_name),
                    generation_metadata=generated.metadata,
                )
            )

        self._notify(progress_callback, "Saving experiment run locally.")
        log_path = self.repository.append(
            {
                "program_text": program_text,
                "precondition": precondition,
                "postcondition": postcondition,
                "user_invariant": user_invariant,
                "syntax_features": core_result.syntax_features,
                "trace": core_result.trace,
                "initial_environment": core_result.initial_environment,
                "user_verification": core_result.user_verification,
                "model_results": model_results,
                "frontend_prompt_addition": frontend_prompt_addition,
                "prompt_style": prompt_style,
                "generation_strategy": generation_strategy,
                "candidate_policy": candidate_policy,
                "temperature": temperature,
                "prompt_refresh_id": prompt_refresh_id,
            }
        )

        return AnalysisResult(
            trace=core_result.trace,
            syntax_features=core_result.syntax_features,
            user_verification=core_result.user_verification,
            model_results=model_results,
            log_path=log_path,
            initial_environment=core_result.initial_environment,
        )

    def analyse_without_models(
        self,
        program_text: str,
        precondition: str,
        postcondition: str,
        user_invariant: str,
        iteration_limit: int,
        progress_callback: Callable[[str], None] | None = None,
        initial_environment: dict[str, int] | None = None,
    ) -> AnalysisResult:
        parsed = self.parse_program(program_text)
        syntax_features = self.extract_syntax_features(parsed)
        initial_environment = self._initial_environment(parsed.variables) | (initial_environment or {})
        if any(type(value) is not int for value in initial_environment.values()):
            raise ValueError("Scalar sample inputs must be integers.")
        if not self.evaluate_precondition(precondition, initial_environment):
            raise ValueError("Scalar sample inputs do not satisfy the precondition before initialization. Supply a valid initial environment.")
        trace = self.trace_executor.execute(parsed, initial_environment, iteration_limit)

        self._notify(progress_callback, "Checking optional manual baseline.")
        checker = self.checker_factory()
        user_verification = checker.verify(
            parsed=parsed,
            precondition=precondition,
            invariant=user_invariant,
            postcondition=postcondition,
        )

        model_results: list[ModelCandidateResult] = []
        self._notify(progress_callback, "Generating trace-derived hypotheses.")
        pattern_candidates = self.pattern_hypothesizer.generate(parsed, trace)
        if pattern_candidates:
            model_results.append(
                ModelCandidateResult(
                    model_name="trace-pattern-hypothesizer",
                    mode="trace-derived",
                    provider="local-patterns",
                    raw_output="\n".join(pattern_candidates),
                    verifications=[
                        self._with_reason(
                            checker.verify(
                                parsed=parsed,
                                precondition=precondition,
                                invariant=candidate,
                                postcondition=postcondition,
                            ),
                            self.pattern_hypothesizer.explain(candidate),
                        )
                        for candidate in pattern_candidates
                    ],
                )
            )

        return AnalysisResult(
            trace=trace,
            syntax_features=syntax_features,
            user_verification=user_verification,
            model_results=model_results,
            log_path="",
            initial_environment=initial_environment,
        )

    def _initial_environment(self, variables: list[str]) -> dict[str, int]:
        # Default sample input values keep trace generation local and deterministic.
        defaults = {"n": 5, "m": 3, "a": 2, "b": 4}
        return {variable: defaults.get(variable, 0) for variable in variables}

    def _notify(
        self,
        progress_callback: Callable[[str], None] | None,
        message: str,
    ) -> None:
        if progress_callback:
            progress_callback(message)

    def _with_reason(self, verification, reason: str):
        return replace(verification, reason=reason)

    def _ai_reason(self, candidate: str, model_name: str, model_mode: str) -> str:
        mode_source = {
            "syntax-only": "the loop syntax: initialisation, guard, body, and variables",
            "trace-only": "the execution trace values",
            "syntax-plus-trace": "both the loop syntax and execution trace values",
        }.get(model_mode, model_mode)
        return (
            f"Model `{model_name}` proposed this from {mode_source}. "
            f"The formula `{candidate}` is treated as a hypothesis and then checked formally."
        )
