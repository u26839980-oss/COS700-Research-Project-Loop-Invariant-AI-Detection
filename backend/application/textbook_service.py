from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from backend.application.ports import ModelPort, RepositoryPort, TextbookCheckerPort
from backend.application.hypothesis_generation import generate_hypotheses
from backend.application.hypothesis_verification import verify_candidates
from backend.domain.models import ExecutionTrace, ModelCandidateResult, VerificationResult
from backend.domain.textbook_contract import resolve_textbook_context, resolve_unassisted_context
from backend.domain.candidate_contract import TEXTBOOK_CANDIDATE_LIMIT, TEXTBOOK_GENERATION_CONTRACT


@dataclass(frozen=True)
class TextbookRequest:
    example_name: str
    benchmark: dict[str, Any]
    program_text: str
    precondition: str
    postcondition: str
    inputs: dict[str, Any]
    iteration_limit: int = 100
    model_names: tuple[str, ...] = ()
    input_mode: str = "syntax-only"
    prompt_style: str = "minimal_frontend"
    frontend_prompt_addition: str = ""
    prompt_refresh_id: str = ""
    clean_evaluation_mode: bool = False
    guidance_examples: list[dict[str, str]] = field(default_factory=list)
    manual_invariant: str = ""
    temperature: float = 0
    max_output_tokens: int = 384
    repeat_penalty: float | None = None


@dataclass(frozen=True)
class TextbookAnalysis:
    trace: ExecutionTrace
    final_state: dict[str, Any]
    reference_verification: VerificationResult
    results: list[ModelCandidateResult]
    log_path: str


class TextbookWorkbenchService:
    def __init__(self, *, execute: Callable, checker: TextbookCheckerPort,
                 build_prompt: Callable, extract_candidates: Callable,
                 repository: RepositoryPort) -> None:
        self.execute = execute
        self.checker = checker
        self.build_prompt = build_prompt
        self.extract_candidates = extract_candidates
        self.repository = repository

    def analyse(self, request: TextbookRequest, adapter: ModelPort,
                progress: Callable[[str], None] | None = None) -> TextbookAnalysis:
        trace, final = self.execute(request.program_text, request.inputs, request.precondition,
                                    request.iteration_limit)
        arrays = tuple(key for key, value in request.benchmark["sample_inputs"].items() if isinstance(value, list))
        context = resolve_unassisted_context(request.program_text, request.precondition)

        def verify(candidate):
            return self.checker.verify(request.example_name, candidate, program=request.program_text,
                precondition=request.precondition, postcondition=request.postcondition, array_names=arrays,
                proof_context=context)

        reference_context = resolve_textbook_context(request.benchmark, request.program_text,
                                                    request.precondition, request.postcondition)
        reference = self.checker.verify(request.example_name, request.benchmark["invariant"],
            program=request.program_text, precondition=request.precondition, postcondition=request.postcondition,
            array_names=arrays, proof_context=reference_context)
        prompt = self.build_prompt(request.example_name, request.benchmark, request.program_text,
            request.precondition, request.postcondition, request.frontend_prompt_addition,
            request.prompt_style, request.prompt_refresh_id, request.clean_evaluation_mode,
            request.guidance_examples, mode=request.input_mode, trace=trace)
        results = []
        if request.manual_invariant.strip():
            results.append(ModelCandidateResult("manual-baseline", request.input_mode, request.manual_invariant,
                [verify(request.manual_invariant)], provider="manual"))
        for model in request.model_names:
            if progress:
                progress(f"Asking model: {model}.")
            generated = generate_hypotheses(adapter, model, prompt, temperature=request.temperature,
                max_output_tokens=request.max_output_tokens, repeat_penalty=request.repeat_penalty)
            checked, origins = verify_candidates(generated.output, self.extract_candidates, verify,
                                                prompt=generated.prompt, candidate_limit=TEXTBOOK_CANDIDATE_LIMIT)
            generated.metadata.update(candidate_limit=TEXTBOOK_CANDIDATE_LIMIT, generation_contract=TEXTBOOK_GENERATION_CONTRACT)
            generated.metadata["candidate_origins"] = origins
            results.append(ModelCandidateResult(model, request.input_mode, generated.output,
                checked, generated.prompt, adapter.provider(model), generated.metadata))
        log_path = self.repository.append({
            "example_name": request.example_name, "benchmark": request.benchmark,
            "program_text": request.program_text, "precondition": request.precondition,
            "postcondition": request.postcondition, "input_mode": request.input_mode,
            "initial_inputs": request.inputs, "trace": trace, "final_state": final,
            "reference_verification": reference, "results": results,
            "verification_contract": context.version, "proof_context": asdict(context),
            "candidate_limit": TEXTBOOK_CANDIDATE_LIMIT, "generation_contract": TEXTBOOK_GENERATION_CONTRACT,
            "prompt_style": request.prompt_style, "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens, "repeat_penalty": request.repeat_penalty,
            "clean_evaluation_mode": request.clean_evaluation_mode,
        })
        return TextbookAnalysis(trace, final, reference, results, log_path)
