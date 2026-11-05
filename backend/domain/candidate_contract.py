"""Declared extraction budgets, shared by prompts, workflows and run records."""

SCALAR_CANDIDATE_LIMIT = 3
SCALAR_GENERATION_CONTRACT = "scalar_candidates_v1"
TEXTBOOK_CANDIDATE_LIMIT = 5
TEXTBOOK_GENERATION_CONTRACT = "textbook_minimal_unassisted_v1"


def validate_candidate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= TEXTBOOK_CANDIDATE_LIMIT:
        raise ValueError(f"Candidate limit must be an integer from 1 to {TEXTBOOK_CANDIDATE_LIMIT}.")
