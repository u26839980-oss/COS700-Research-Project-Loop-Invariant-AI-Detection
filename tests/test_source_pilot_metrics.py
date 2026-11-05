import unittest

from backend.application.source_pilot_metrics import benchmark_role, classify, summarize


class SourcePilotMetricsTests(unittest.TestCase):
    def test_controls_not_positive(self):
        self.assertEqual(benchmark_role("code2inv-026"), "unsafe_control")
        self.assertEqual(benchmark_role("svcomp-loops-array-2"), "unsafe_control")
        self.assertEqual(benchmark_role("svcomp-loop-lit-css2003"), "native_safety_profile_mismatch")
        self.assertEqual(benchmark_role("code2inv-030"), "positive")

    def test_provider_errors_not_reasoning_failures(self):
        self.assertEqual(classify(dict(status="invalid_model_output", generation=dict(
            metadata=dict(provider_error="timeout")))), "provider_or_resource_failure")

    def test_unsupported_not_refutation(self):
        self.assertEqual(classify(dict(status="checked_original_SMT_Int_VCs", verifications=[
            dict(outcome_kind="checker_limitation")])), "checker_limitation_or_inconclusive")

    def test_unknown_symbols_are_bad_candidates(self):
        self.assertEqual(classify(dict(status="checked_original_SMT_Int_VCs", verifications=[dict(
            outcome_kind="checker_limitation", limitation_scope="candidate_expression",
            error="Candidate uses variables outside the upstream invariant signature.")])), "model_candidate_failure")

    def test_inductive_not_full_proof(self):
        self.assertEqual(classify(dict(status="checked_original_SMT_Int_VCs", verifications=[
            dict(is_inductive=True, accepted=False)])), "inductive_insufficient")

    def test_best_candidate_proof_wins(self):
        self.assertEqual(classify(dict(status="checked_original_SMT_Int_VCs", verifications=[
            dict(outcome_kind="candidate_refuted"), dict(accepted=True)])), "full_proof")

    def test_truncation_kept_separate(self):
        self.assertEqual(classify(dict(status="invalid_model_output", generation=dict(
            metadata=dict(done_reason="length")))), "generation_truncated")

    def test_bounded_safety_is_not_invariant_proof(self):
        self.assertEqual(classify(dict(status="safety_proved_complete_unwinding")), "safety_only_no_invariant_proof")

    def test_context_limit_has_no_model_call(self):
        row = dict(model="local", mode="trace-only", budget=25, benchmark_role="positive",
                   outcome="context_limit", model_calls=0)
        summary = summarize([row], 2)
        self.assertEqual((summary["completed"], summary["pending"], summary["model_calls"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
