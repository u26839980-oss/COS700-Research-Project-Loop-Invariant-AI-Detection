"""Native response extraction tests; no inference or formal tools are invoked."""
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from backend.infrastructure.candidate_parser import extract_native_contracts, extract_strict_native_contracts


class NativeCandidateParserTests(unittest.TestCase):
    def test_original_policy_and_corrected_policy_remain_distinct(self):
        raw = '{"0":"x>=0"}'
        self.assertEqual(extract_strict_native_contracts(raw), extract_native_contracts(raw))
        with self.assertRaises(ValueError):
            extract_strict_native_contracts('```json\n'+raw+'\n```')

    def test_service_admits_fence_without_changing_saved_output_or_predicate(self):
        from bootstrap import build_source_benchmark_service
        raw = '```json\n{"0":"x%4==0"}\n```'
        adapter = SimpleNamespace(generate=lambda *args, **kwargs: raw, last_generation_metadata={})
        service = build_source_benchmark_service()
        with tempfile.TemporaryDirectory() as directory:
            service.repository.results = Path(directory)
            with patch.object(service, 'check', return_value={'status': 'test-only'}) as check:
                result = service.generate('svcomp-loop-invariants-mod4', 'test-only-adapter', adapter,
                                          context_length=32768, max_output_tokens=2048)
            self.assertEqual(result, {'status': 'test-only'})
            self.assertEqual(check.call_args.args[1], {'0': 'x%4==0'})
            self.assertEqual(check.call_args.kwargs['generation']['raw_output'], raw)

    def test_raw_and_single_fenced_json_preserve_exact_predicates(self):
        raw = '{"0":" x >= 0 && x <= n ","1":"forall(k, k < 0 || a[k] == 0)"}'
        expected = {"0": " x >= 0 && x <= n ", "1": "forall(k, k < 0 || a[k] == 0)"}
        for value in [raw, '```json\n'+raw+'\n```', '```\r\n'+raw+'\r\n```',
                      ' \n```JSON\n'+raw+'\n```\n ']:
            with self.subTest(value=value):
                self.assertEqual(extract_native_contracts(value), expected)

    def test_prose_multiple_fences_truncated_fences_and_wrong_languages_fail(self):
        for value in ['Here it is:\n```json\n{"0":"x>=0"}\n```',
                      '```json\n{"0":"x>=0"}\n```\nExplanation',
                      '```json\n{"0":"x>=0"}', '```python\n{"0":"x>=0"}\n```',
                      '```json\n{"0":"x>=0"}\n```\n```json\n{"0":"True"}\n```']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_native_contracts(value)

    def test_invalid_schemas_and_duplicate_keys_fail_closed(self):
        for value in ['{}', '[]', 'null', '{"0":true}', '{"0":{}}', '{"0":" "}',
                      '{"0":"x>=0","0":"x<0"}']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                extract_native_contracts(value)


if __name__ == '__main__':
    unittest.main()
