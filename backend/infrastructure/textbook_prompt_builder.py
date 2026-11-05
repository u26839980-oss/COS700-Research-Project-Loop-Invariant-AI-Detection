"""Minimal target-only prompts; benchmark answers and source hints are never read."""
from backend.domain.candidate_contract import TEXTBOOK_CANDIDATE_LIMIT
from backend.infrastructure.prompt_sections import trace_sections


def build_book_benchmark_prompt(
    example_name, benchmark, program_text, precondition, postcondition,
    frontend_prompt_addition, prompt_style, prompt_refresh_id,
    clean_evaluation_mode, guidance_examples, mode="syntax-only", trace=None,
    *, preview=False,
):
    # Legacy style/guidance arguments remain compatible, but cannot inject test answers.
    sections = [
        "Generate inductive loop invariants: they must hold initially and be preserved by each iteration.",
        f"Precondition: {precondition}",
        f"Postcondition: {postcondition}",
    ]
    if mode in {"syntax-only", "syntax-plus-trace"}:
        sections.extend(["PROGRAM:", program_text])
    if mode in {"trace-only", "syntax-plus-trace"}:
        if trace is None or not trace.rows:
            raise ValueError("Trace input mode requires actual execution states.")
        sections.extend(trace_sections(trace, full=True))
        sections.append("Trace rows are outer loop-head states, including exit, before post-loop statements. The iteration column is not a variable.")
    if mode not in {"syntax-only", "trace-only", "syntax-plus-trace"}:
        raise ValueError("Unknown input mode.")
    if frontend_prompt_addition.strip():
        sections.extend(["Additional user instructions:", frontend_prompt_addition.strip()])
    sections.extend([
        "Use Python predicates: and, or, not, ==, //, **, len, max, slices, in/not in, all/any over range. Element indices must be nonnegative and in range.",
        f"Return at most {TEXTBOOK_CANDIDATE_LIMIT} complete formulas, one per line, between CANDIDATES and END. No explanations or markdown. Put END on its own line.",
    ])
    return "\n".join(sections)
