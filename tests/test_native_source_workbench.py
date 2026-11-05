"""Native solver integration and UI tests. These tests never call an LLM."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ("pcpp", "pycparser", "sexpdata"))
if AVAILABLE:
    from bootstrap import build_source_benchmark_service
    from backend.infrastructure.cbmc_checker import CBMCChecker, classify_output
    from backend.infrastructure.svcomp_contracts import ContractError, prepare_source, validate_expression
from backend.application.source_experiment_plan import make_plan


class ExperimentPlanningTests(unittest.TestCase):
    def test_syntax_not_multiplied_by_trace_budgets(self):
        plan = make_plan(["a", "b"], ["m1", "m2"], repeats=5,
                         modes=["syntax-only", "trace-only", "syntax-plus-trace"], trace_budgets=[5, 10, 15])
        self.assertEqual(plan["planned_generation_calls"], 2 * 2 * 5 * (1 + 3 + 3))
        self.assertEqual(plan["distinct_tasks"], 2)
        self.assertEqual(plan["model_calls"], 0)
        self.assertFalse(plan["run_ready"])
        self.assertEqual(plan["power_planning"]["approximate_required_distinct_task_pairs"], 236)

    def test_invalid_limits_fail_closed(self):
        for kwargs in ({"repeats": 0}, {"modes": ["invented"]}, {"trace_budgets": [-1]}, {"minimum_effect": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                make_plan(["a"], ["m"], **kwargs)


@unittest.skipUnless(AVAILABLE, "Install requirements.txt")
class NativeSourceWorkbenchTests(unittest.TestCase):
    def test_all_sources_inspect_and_prompt_without_answers_or_provenance(self):
        service = build_source_benchmark_service()
        for task in service.repository.catalog():
            with self.subTest(task=task["id"]):
                info = service.inspect(task["id"])
                prompt = service.prompt(task["id"])
                self.assertTrue(info["loops"])
                self.assertNotIn(task["id"], prompt)
                self.assertNotIn("http", prompt)
                self.assertNotIn("upstream_expected_verdict", prompt)
                self.assertNotIn("certificate", prompt)
                self.assertNotIn("//", prompt)

    def test_predicates_reject_calls_mutations_and_statement_injection(self):
        for text in ("x++", "(x=0)", "abort()", "__CPROVER_assume(0)", "0); } int x; void evil(){ int y=(1", "forall(k, x++)"):
            with self.subTest(text=text), self.assertRaises(ContractError):
                validate_expression(text)
        self.assertIn("__CPROVER_forall", validate_expression("forall(k, k<0 || a[k]==b[k])"))

    def test_array_assigns_covers_whole_object(self):
        source = "int main(){int a[10]; int i; for(i=0;i<10;i++){a[i]=i;}}"
        prepared, _ = prepare_source(source, {"0": "i>=0 && i<=10"})
        self.assertIn("__CPROVER_object_whole(a)", prepared)
        self.assertLess(prepared.index("__CPROVER_assigns"), prepared.index("__CPROVER_loop_invariant"))

    def test_unknown_helper_calls_are_not_silently_discarded(self):
        with self.assertRaises(ContractError):
            prepare_source("int main(){unmodelled();}")

    def test_json_classification_never_accepts_errors_or_incomplete_unwind(self):
        success = {"result": [{"property": "goal.1", "status": "SUCCESS"}]}
        self.assertFalse(classify_output([success, {"messageType": "ERROR"}], 0, False)["accepted"])
        self.assertEqual(classify_output([success], 0, 1)["status"], "missing_contract_obligations")
        incomplete = {"result": [{"property": "main.unwind.0", "status": "FAILURE"}]}
        self.assertEqual(classify_output([success, incomplete], 10, False)["status"], "incomplete_unwinding")

    @unittest.skipUnless(AVAILABLE and CBMCChecker().available(), "Install CBMC for real native integration checks")
    def test_real_native_unsigned_contract_and_counterexample(self):
        service = build_source_benchmark_service()
        with tempfile.TemporaryDirectory() as directory:
            checker = CBMCChecker()
            source = service.repository.read("svcomp-loop-invariants-mod4")
            good = checker.run(source, Path(directory) / "good", contracts={"0": "x%4==0"}, timeout=10)
            self.assertEqual(good["status"], "contracts_and_safety_proved", good)
            bad = checker.run(source, Path(directory) / "bad", contracts={"0": "x==0"}, timeout=10)
            self.assertEqual(bad["status"], "contract_refuted", bad)
            # No contract and a small unwind must not become an unbounded proof.
            bounded = checker.run(source, Path(directory) / "bounded", unwind=2, timeout=10)
            self.assertEqual(bounded["status"], "incomplete_unwinding", bounded)
            self.assertEqual(good["model_calls"], 0)

    def test_source_workspace_loads_without_contacting_models(self):
        from streamlit.testing.v1 import AppTest
        with patch("backend.infrastructure.model_adapter.ModelAdapter.list_models", return_value=[]), \
             patch("backend.infrastructure.model_adapter.ModelAdapter.generate", side_effect=AssertionError("No model calls")):
            app = AppTest.from_file(str(ROOT / "app.py"))
            app.query_params["workspace"] = "sources"
            app.run(timeout=20)
            self.assertEqual(list(app.exception), [])
            self.assertTrue(any(item.label == "Source task" for item in app.selectbox))
            next(item for item in app.selectbox if item.label == "Source collection").set_value("SV-COMP").run(timeout=20)
            self.assertEqual(list(app.exception), [])
            self.assertTrue(any("Loop 0" in item.label for item in app.text_area))

    def test_code2inv_service_saves_actual_proof_without_models_or_timestamp(self):
        service = build_source_benchmark_service()
        with tempfile.TemporaryDirectory() as directory:
            service.repository.results = Path(directory)
            result = service.check("code2inv-100", ["x+y==n and x>=0"])
            self.assertTrue(result["verifications"][0]["accepted"])
            self.assertEqual(result["model_calls"], 0)
            saved = json.loads(Path(result["log_path"]).read_text())
            self.assertEqual(saved["origin"], "manual")
            self.assertNotIn("timestamp", saved)

    @unittest.skipUnless(AVAILABLE and CBMCChecker().available(), "Install CBMC")
    def test_native_nested_loop_linear_contracts(self):
        service = build_source_benchmark_service()
        contracts = json.loads((ROOT / "tests/fixtures/native_checker_contracts.json").read_text())["svcomp-loop-new-nested-1"]
        with tempfile.TemporaryDirectory() as directory:
            result = CBMCChecker().run(service.repository.read("svcomp-loop-new-nested-1"),
                Path(directory) / "nested", contracts=contracts, timeout=20)
            self.assertEqual(result["status"], "contracts_and_safety_proved", result)

    @unittest.skipUnless(AVAILABLE and CBMCChecker().available(), "Install CBMC")
    def test_false_contract_and_original_unsafe_array_are_distinct(self):
        service = build_source_benchmark_service()
        with tempfile.TemporaryDirectory() as directory:
            result = CBMCChecker().run(service.repository.read("svcomp-loops-array-2"),
                Path(directory) / "unsafe", unwind=3, timeout=10)
            self.assertEqual(result["status"], "safety_counterexample", result)
            self.assertFalse(result["invariant_proved"])
            partial = CBMCChecker().run(service.repository.read("svcomp-loops-array-2"),
                Path(directory) / "partial", contracts={"0": "SIZE == 1 && j <= SIZE"}, timeout=10)
            self.assertEqual(partial["status"], "insufficient_for_goal", partial)
            self.assertTrue(partial["invariant_proved"], partial)
            self.assertFalse(partial["accepted"])

    @unittest.skipUnless(AVAILABLE and CBMCChecker().available(), "Install CBMC")
    def test_real_timeout_is_resource_limited_not_refutation(self):
        service = build_source_benchmark_service()
        with tempfile.TemporaryDirectory() as directory:
            result = CBMCChecker().run(service.repository.read("svcomp-loop-invariants-eq1"),
                Path(directory) / "timeout", unwind=1000, timeout=1)
            self.assertEqual(result["status"], "timeout", result)
            self.assertFalse(result["accepted"])

    @unittest.skipUnless(AVAILABLE and CBMCChecker().available(), "Install CBMC")
    def test_real_quantified_large_copy_contracts(self):
        service = build_source_benchmark_service()
        contracts = json.loads((ROOT / "tests/fixtures/native_checker_contracts.json").read_text())["svcomp-array-examples-standard_copy1_ground-1"]
        with tempfile.TemporaryDirectory() as directory:
            result = CBMCChecker().run(service.repository.read("svcomp-array-examples-standard_copy1_ground-1"),
                Path(directory) / "copy", contracts=contracts, timeout=20)
            if result["status"] == "quantifier_solver_unavailable":
                self.skipTest("Install the external Z3 executable for native quantifier checks")
            self.assertEqual(result["status"], "contracts_and_safety_proved", result)


if __name__ == "__main__":
    unittest.main()
