"""Composition root: concrete adapters are wired here, not in application services."""
from backend.application.invariant_service import InvariantWorkbenchService
from backend.application.textbook_service import TextbookWorkbenchService
from backend.infrastructure.benchmark_z3_checker import BenchmarkZ3Checker
from backend.infrastructure.candidate_parser import extract_book_candidates
from backend.infrastructure.textbook_prompt_builder import build_book_benchmark_prompt
from backend.infrastructure.textbook_runtime import execute
from backend.infrastructure.experiment_repository import ExperimentRepository
from backend.infrastructure.loop_parser import extract_syntax_features, parse_program, parse_expression
from backend.infrastructure.pattern_hypothesizer import PatternHypothesizer
from backend.infrastructure.prompt_builder import PromptBuilder
from backend.infrastructure.trace_executor import TraceExecutor, SafeExpressionEvaluator
from backend.infrastructure.z3_checker import Z3InvariantChecker


def build_workbench_service() -> InvariantWorkbenchService:
    return InvariantWorkbenchService(
        trace_executor=TraceExecutor(), prompt_builder=PromptBuilder(),
        pattern_hypothesizer=PatternHypothesizer(), repository=ExperimentRepository(),
        checker_factory=Z3InvariantChecker, parse_program=parse_program,
        extract_syntax_features=extract_syntax_features,
        evaluate_precondition=lambda predicate, inputs: bool(SafeExpressionEvaluator(inputs).visit(parse_expression(predicate))),
    )


def build_textbook_service(repository=None) -> TextbookWorkbenchService:
    return TextbookWorkbenchService(execute=execute, checker=BenchmarkZ3Checker(),
        build_prompt=build_book_benchmark_prompt, extract_candidates=extract_book_candidates,
        repository=repository if repository is not None else ExperimentRepository())


def build_source_benchmark_service(repository=None, native_contract_parser=None):
    from backend.application.source_benchmark_service import SourceBenchmarkService
    from backend.infrastructure.source_benchmark_repository import SourceBenchmarkRepository
    from backend.infrastructure.code2inv_source_checker import Code2InvSourceChecker
    from backend.infrastructure.cbmc_checker import CBMCChecker
    from backend.infrastructure.svcomp_contracts import describe_source
    from backend.infrastructure.candidate_parser import extract_scalar_candidates, extract_native_contracts
    from backend.infrastructure.source_trace_sampler import SourceTraceSampler
    return SourceBenchmarkService(repository=repository if repository is not None else SourceBenchmarkRepository(), code_checker_factory=Code2InvSourceChecker,
        native_checker=CBMCChecker(), prepare_source=describe_source, parse_candidates=extract_scalar_candidates,
        parse_native_contracts=native_contract_parser if native_contract_parser is not None else extract_native_contracts,
        trace_sampler=SourceTraceSampler())


def build_source_model_adapter():
    from backend.infrastructure.model_adapter import ModelAdapter
    return ModelAdapter()
