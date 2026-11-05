"""Portable runtime verification. No inference requests or original-result writes."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def check(build_only=False, skip_native=False):
    from bootstrap import build_source_benchmark_service
    from backend.domain.source_traces import TraceSamplingOptions
    from backend.infrastructure.cbmc_checker import CBMCChecker

    service = build_source_benchmark_service()
    tasks = service.repository.catalog()
    assert len(tasks) == 42
    assert sum(task['collection'] == 'Code2Inv' for task in tasks) == 30
    checked = set()
    manifest = service.repository.manifest
    for asset in manifest.get('provenance_assets', []) + [a for t in tasks for a in t['assets']]:
        path = service.repository.bundle / asset['path']
        assert path.resolve().is_relative_to(service.repository.bundle.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256'], path
        checked.add(asset['path'])
    for task in tasks:
        assert service.inspect(task['id'])['loops'], task['id']
        assert 'CANDIDATES' in service.prompt(task['id']) or task['collection'] == 'SV-COMP'
    result = dict(benchmark_tasks=42, hashed_assets=len(checked), model_calls=0)
    if not skip_native:
        result['tools'] = {}
        for tool, flag in [('cbmc', '--version'), ('goto-cc', '--version'),
                           ('goto-instrument', '--version'), ('z3', '--version')]:
            executable = shutil.which(tool)
            assert executable, tool + ' missing'
            completed = subprocess.run([executable, flag], capture_output=True, text=True, timeout=20, check=True)
            result['tools'][tool] = completed.stdout.strip()
        from backend.infrastructure.native_trace_sampler import NativeTraceSampler
        zig = NativeTraceSampler().compiler
        result['tools']['zig'] = subprocess.run([zig, 'version'], capture_output=True, text=True,
                                                timeout=20, check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as directory:
        service.repository.results = Path(directory) / 'source'
        good = service.check('code2inv-100', ['x + y == n and x >= 0'])['verifications'][0]
        assert good['proves_postcondition'] is True, good
        bad = service.check('code2inv-100', ['False'])['verifications'][0]
        assert bad['establishment'] != 'proved', bad
        result['integer_positive_and_negative_checks'] = 'passed'
        if not build_only:
            options = TraceSamplingOptions(rows_per_loop=5, samples=1)
            sampled = service.sample_traces('code2inv-100', options)
            assert sampled['status'] == 'sampled_all_loops', sampled['status']
            for mode in ['syntax-only', 'trace-only', 'syntax-plus-trace']:
                assert service.prompt('code2inv-100', input_mode=mode, trace_record=sampled)
            result['integer_traces_and_three_modes'] = 'passed'
            if not skip_native:
                contracts = {'0': 'w == x && y == z'}
                native = CBMCChecker().run(service.repository.read('svcomp-loop-invariants-eq1'),
                    Path(directory) / 'native', contracts=contracts, timeout=60)
                assert native['status'] == 'contracts_and_safety_proved', native
                sampled = service.sample_traces('svcomp-loop-invariants-eq1', options)
                assert sampled['status'] == 'sampled_all_loops', sampled['status']
                result['native_contract_and_compiled_trace'] = 'passed'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-check', action='store_true')
    parser.add_argument('--skip-native', action='store_true', help='Local source check only; not container acceptance')
    args = parser.parse_args()
    print(json.dumps(check(args.build_check, args.skip_native), indent=2))
