import unittest
from unittest.mock import Mock

from backend.application.hypothesis_verification import verify_candidates
from backend.application.response_validation import candidate_response
from backend.infrastructure.candidate_parser import extract_scalar_candidates
from backend.infrastructure.research_benchmarks import scaling_case
from backend.infrastructure.loop_parser import parse_program
from backend.infrastructure.z3_checker import Z3InvariantChecker
from scripts.run_research_experiment import failure_category


class ResponseValidationTests(unittest.TestCase):
    prompt = (
        "You are helping generate loop invariant hypotheses.\n"
        "A loop invariant must hold at the initial loop head and be preserved by every loop iteration. "
        "Prefer facts relevant to the postcondition; they need not prove the entire goal alone.\n"
        "PRECONDITION:\nn >= 0\nPOSTCONDITION:\ny == n\n"
    )

    def test_prompt_echo_cannot_turn_specifications_into_hypotheses(self):
        verify = Mock()
        results, origins = verify_candidates(self.prompt, extract_scalar_candidates, verify, prompt=self.prompt)
        self.assertEqual((results, origins), ([], {}))
        verify.assert_not_called()

    def test_actual_candidates_before_later_echo_remain_checkable(self):
        raw = "CANDIDATES:\nx + y == n and x >= 0\nEND:\n" + self.prompt
        case = scaling_case("transfer", 5)
        checker = Z3InvariantChecker()
        results, origins = verify_candidates(raw, extract_scalar_candidates,
            lambda candidate: checker.verify(parse_program(case["program_text"]),
                case["precondition"], candidate, case["postcondition"]), prompt=self.prompt)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].proves_postcondition)
        self.assertEqual(list(origins.values()), ["model_output"])

    def test_partial_prompt_echo_is_also_rejected(self):
        cleaned, echoed = candidate_response(self.prompt[:220], self.prompt)
        self.assertTrue(echoed)
        self.assertEqual(cleaned, "")

    def test_short_prompt_or_shared_invariant_does_not_cause_false_echo(self):
        self.assertEqual(candidate_response("x >= 0", "x >= 0"), ("x >= 0", False))
        self.assertEqual(candidate_response("n >= 0", self.prompt), ("n >= 0", False))

    def test_echo_is_a_provenance_failure_not_a_logical_refutation(self):
        self.assertEqual(failure_category(self.prompt, [], {"response_echo_detected": True}), "prompt_echo")


if __name__ == "__main__":
    unittest.main()
