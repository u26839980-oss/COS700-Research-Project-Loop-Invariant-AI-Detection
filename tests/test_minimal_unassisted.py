import unittest

from backend.domain.benchmarks import EXAMPLES
from backend.domain.textbook_contract import resolve_unassisted_context
from backend.infrastructure.array_z3_checker import ArrayZ3Checker
from backend.infrastructure.textbook_prompt_builder import build_book_benchmark_prompt
from backend.infrastructure.textbook_runtime import execute
from backend.infrastructure.textbook_runtime import evaluate
from backend.infrastructure.textbook_input_scaling import textbook_inputs_at_size


class MinimalUnassistedTests(unittest.TestCase):
    def test_all_styles_and_modes_ignore_answers_bounds_names_and_guidance(self):
        for name, example in EXAMPLES.items():
            if not example.get("book_benchmark"):
                continue
            trace, _ = execute(example["program"], example["sample_inputs"], example["precondition"])
            poisoned = {**example, "loop_head_bounds": "SECRET_BOUND", "input_domain_notes": "SECRET_HINT",
                        "invariant": "SECRET_REFERENCE", "source": "SECRET_SOURCE"}
            guidance = [{"name": "SECRET_EXAMPLE", "pattern": "SECRET_PATTERN", "invariant": "SECRET_ANSWER"}]
            for style in ("minimal_frontend", "guided_self_check", "original_few_shot"):
                for mode in ("syntax-only", "trace-only", "syntax-plus-trace"):
                    prompt = build_book_benchmark_prompt("SECRET_NAME", poisoned, example["program"],
                        example["precondition"], example["postcondition"], "", style, "SECRET_REFRESH",
                        False, guidance, mode, trace)
                    self.assertNotIn("SECRET_", prompt)
                    self.assertNotIn("Supplied loop-head", prompt)
                    self.assertEqual("PROGRAM:" in prompt, mode != "trace-only")
                    self.assertEqual("TRACE:" in prompt, mode != "syntax-only")

    def test_no_hidden_source_bounds_in_verification(self):
        example = EXAMPLES["Linear Search"]
        context = resolve_unassisted_context(example["program"], example["precondition"])
        self.assertEqual(context.loop_head_bounds, "True")
        result = ArrayZ3Checker().verify_program(example["program"], example["precondition"],
            example["book_invariant"], example["postcondition"], proof_context=context)
        self.assertFalse(result.accepted)
        self.assertEqual(result.preservation, "failed")

    def test_every_scaled_textbook_input_satisfies_domain_and_goal(self):
        for name, example in EXAMPLES.items():
            if not example.get("book_benchmark"):
                continue
            for size in (5, 10, 15, 20, 25):
                with self.subTest(name=name, size=size):
                    inputs = textbook_inputs_at_size(name, example, size)
                    self.assertEqual(inputs["n"] if name == "Exponentiation" else len(inputs["A"]), size)
                    trace, final = execute(example["program"], inputs, example["precondition"])
                    self.assertTrue(evaluate(example["postcondition"], final))
                    self.assertTrue(all(evaluate(example["invariant"], row) for row in trace.rows))

    def test_full_traces_are_not_silently_capped(self):
        example = EXAMPLES["Finding the Maximal Element"]
        inputs = textbook_inputs_at_size("Finding the Maximal Element", example, 25)
        trace, _ = execute(example["program"], inputs, example["precondition"])
        prompt = build_book_benchmark_prompt("ignored", {}, example["program"], example["precondition"],
            example["postcondition"], "", "minimal_frontend", "", True, [], "trace-only", trace)
        self.assertGreater(len(trace.rows), 13)
        self.assertNotIn("middle trace rows omitted", prompt)
        self.assertEqual(len(prompt.split("TRACE:\n", 1)[1].split("\nTrace rows", 1)[0].splitlines()) - 2, len(trace.rows))

    def test_syntax_control_is_identical_across_input_sizes(self):
        example = EXAMPLES["Linear Search"]
        prompts = []
        for size in (5, 25):
            trace, _ = execute(example["program"], textbook_inputs_at_size("Linear Search", example, size), example["precondition"])
            prompts.append(build_book_benchmark_prompt("ignored", example, example["program"], example["precondition"],
                example["postcondition"], "", "minimal_frontend", "", True, [], "syntax-only", trace))
        self.assertEqual(prompts[0], prompts[1])


if __name__ == "__main__":
    unittest.main()
