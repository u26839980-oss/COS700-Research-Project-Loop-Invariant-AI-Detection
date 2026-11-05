import unittest
from copy import deepcopy
from itertools import combinations_with_replacement

from app import EXAMPLES, build_book_benchmark_prompt, build_guidance_examples, extract_book_candidates
from backend.infrastructure.benchmark_z3_checker import BenchmarkZ3Checker
from backend.infrastructure.textbook_runtime import execute, evaluate


class TextbookExampleTests(unittest.TestCase):
    def check(self, name, invariant=None, program=None):
        example = EXAMPLES[name]
        return BenchmarkZ3Checker().verify(
            name, invariant or example["invariant"], program=program or example["program"],
            precondition=example["precondition"], postcondition=example["postcondition"],
            array_names=tuple(key for key, value in example["sample_inputs"].items() if isinstance(value, list)))

    def test_array_references_are_proved_not_matched(self):
        for name in ("Linear Search", "Finding the Maximal Element", "Binary Search"):
            with self.subTest(name=name):
                result = self.check(name)
                self.assertTrue(result.accepted, result)

    def test_every_example_executes_and_reference_holds_on_sample(self):
        for name, example in EXAMPLES.items():
            if not example.get("book_benchmark"):
                continue
            with self.subTest(name=name):
                inputs = deepcopy(example["sample_inputs"])
                trace, final = execute(example["program"], inputs, example["precondition"])
                self.assertTrue(evaluate(example["postcondition"], final))
                self.assertTrue(all(evaluate(example["invariant"], row) for row in trace.rows))
                self.assertEqual(inputs, example["sample_inputs"])

    def test_wrong_and_unsafe_search_invariants_do_not_pass(self):
        for invariant in ("i == 0", "True", "x not in A", "A[i] == x"):
            with self.subTest(invariant=invariant):
                self.assertFalse(self.check("Linear Search", invariant).accepted)

    def test_changed_search_program_cannot_borrow_reference_proof(self):
        example = EXAMPLES["Linear Search"]
        wrong = example["program"].replace("i = i - 1", "i = i + 1")
        result = self.check("Linear Search", program=wrong)
        self.assertFalse(result.accepted)
        self.assertEqual(result.preservation, "failed")

    def test_equivalent_alternative_reference_can_pass(self):
        result = self.check("Linear Search", "x in A and 0 <= i and i < len(A) and all(A[k] != x for k in range(i + 1, len(A)))")
        self.assertTrue(result.accepted, result)

    def test_negative_indices_and_unsafe_predicates_are_not_valid_array_accesses(self):
        for expression in ("A[-1] == x", "A[len(A)] == x"):
            result = self.check("Linear Search", expression)
            self.assertFalse(result.accepted)
            self.assertEqual(result.establishment, "failed")

    def test_array_lengths_do_not_alias_user_scalar_names(self):
        from backend.infrastructure.array_z3_checker import ArrayZ3Checker
        program = "i = length_A\nwhile i < 0:\n    i = i + len(A)"
        result = ArrayZ3Checker().verify_program(program, "True", "i == len(A)", "True")
        self.assertFalse(result.accepted)
        self.assertEqual(result.establishment, "failed")

    def test_max_reference_rejects_wrong_branch_update(self):
        example = EXAMPLES["Finding the Maximal Element"]
        result = self.check("Finding the Maximal Element", program=example["program"].replace("i = j", "i = 0"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.preservation, "failed")

    def test_binary_search_checks_post_loop_result_assignment(self):
        example = EXAMPLES["Binary Search"]
        result = self.check("Binary Search", program=example["program"].replace("i = l", "i = -1"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.postcondition, "failed")

    def test_slice_colons_and_end_identifiers_survive_candidate_extraction(self):
        output = "CANDIDATES\nA[i] == max(A[0:j]) and j <= END_BOUND\nEND"
        self.assertEqual(extract_book_candidates(output), ["A[i] == max(A[0:j]) and j <= END_BOUND"])

    def test_prose_is_not_a_candidate_and_multiline_predicates_are_preserved(self):
        output = "A complete invariant is:\n1. `max(A[:i]) == max(A[:j])` and `i >= 0`"
        self.assertEqual(extract_book_candidates(output), ["max(A[:i]) == max(A[:j]) and i >= 0"])
        self.assertEqual(extract_book_candidates("CANDIDATES\n(i >= 0\n and i < len(A))\nEND"),
                         ["i >= 0 and i < len(A)"])
        self.assertEqual(extract_book_candidates("A = [1, 2]\n42\nA = [2] -> A[i] == x"), [])

    def test_pattern_short_empty_inputs_and_exponent_samples(self):
        pattern = EXAMPLES["Pattern Matching"]
        for array, needle in (([], [1]), ([1], [1, 2]), ([1, 2], [1, 2])):
            _, final = execute(pattern["program"], {"A": array, "p": needle, "M": [0] * len(array)}, pattern["precondition"])
            self.assertTrue(evaluate(pattern["postcondition"], final))
        exponent = EXAMPLES["Exponentiation"]
        for base in (-2, 0, 3):
            for power in (1, 2, 5, 12):
                trace, final = execute(exponent["program"], {"a": base, "n": power}, exponent["precondition"])
                self.assertTrue(evaluate(exponent["postcondition"], final))
                self.assertTrue(all(evaluate(exponent["invariant"], row) for row in trace.rows))

    def test_sample_preconditions_and_resource_limits_are_enforced(self):
        example = EXAMPLES["Linear Search"]
        with self.assertRaises(ValueError):
            execute(example["program"], {"A": [1, 2], "x": 9}, example["precondition"])
        with self.assertRaises(Exception):
            execute("i = 0\nwhile i < 2:\n    i = i", {}, "True", iteration_limit=2)
        with self.assertRaises(Exception):
            evaluate("2 ** (2 ** 10000) == 0", {})

    def test_binary_search_empty_absent_and_present_inputs(self):
        example = EXAMPLES["Binary Search"]
        for array, target in (([], 4), ([2], 2), ([2], 1), ([1, 2, 2, 4], 2), ([1, 3, 8], 9)):
            trace, final = execute(example["program"], {"A": array, "x": target}, example["precondition"])
            self.assertTrue(evaluate(example["postcondition"], final))
            self.assertTrue(all(evaluate(example["invariant"], row) for row in trace.rows))

    def test_binary_search_exhaustive_small_ordered_arrays(self):
        example = EXAMPLES["Binary Search"]
        for size in range(5):
            for values in combinations_with_replacement(range(3), size):
                for target in range(-1, 4):
                    trace, final = execute(example["program"], {"A": list(values), "x": target}, example["precondition"])
                    self.assertTrue(evaluate(example["postcondition"], final), (values, target, final))
                    self.assertTrue(all(evaluate(example["invariant"], row) for row in trace.rows))

    def test_pattern_includes_last_start_and_rejects_empty_pattern(self):
        example = EXAMPLES["Pattern Matching"]
        _, final = execute(example["program"], {"A": [9, 1, 2], "p": [1, 2], "M": [0, 0, 0]}, example["precondition"])
        self.assertEqual(final["M"][:2], [-1, 1])
        with self.assertRaises(ValueError):
            execute(example["program"], {"A": [1], "p": [], "M": [0]}, example["precondition"])

    def test_extended_proofs_use_explicit_context(self):
        from backend.domain.textbook_contract import resolve_textbook_context
        for name in ("Pattern Matching", "Exponentiation"):
            example = EXAMPLES[name]
            result = BenchmarkZ3Checker().verify(name, example["invariant"], program=example["program"],
                precondition=example["precondition"], postcondition=example["postcondition"],
                array_names=tuple(k for k, v in example["sample_inputs"].items() if isinstance(v, list)),
                proof_context=resolve_textbook_context(example, example["program"], example["precondition"], example["postcondition"]))
            self.assertTrue(result.accepted, result)

    def test_no_target_reference_in_guidance_for_either_benchmark_mode(self):
        for name in EXAMPLES:
            guidance = build_guidance_examples(name, True)
            self.assertNotIn(name, [entry["name"] for entry in guidance])

    def test_book_prompt_modes_have_actual_traces_without_source_leakage(self):
        example = EXAMPLES["Linear Search"]
        trace, _ = execute(example["program"], example["sample_inputs"], example["precondition"])
        for mode in ("syntax-only", "trace-only", "syntax-plus-trace"):
            prompt = build_book_benchmark_prompt("Linear Search", example, example["program"],
                example["precondition"], example["postcondition"], "", "guided_self_check", "", True, [], mode, trace)
            self.assertEqual("PROGRAM:" in prompt, mode != "trace-only")
            self.assertEqual("TRACE:" in prompt, mode != "syntax-only")
            self.assertNotIn(example["invariant"], prompt)
            self.assertNotIn("Reasoning cue:", prompt)
            self.assertNotIn("Linear Search", prompt)


if __name__ == "__main__":
    unittest.main()
