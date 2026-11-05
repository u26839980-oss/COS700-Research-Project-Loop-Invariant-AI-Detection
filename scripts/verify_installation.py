"""Check source integrity, trace modes and formal tools without calling a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap import build_source_benchmark_service
from backend.domain.source_traces import TraceSamplingOptions
from backend.infrastructure.source_benchmark_repository import SourceBenchmarkRepository


def verify(bundle=None, native=False):
    supplied = SourceBenchmarkRepository(bundle=bundle) if bundle is not None else None
    service = build_source_benchmark_service(repository=supplied)
    repository = service.repository
    tasks = repository.catalog()
    assert len(tasks)==42
    assets = {a['path']:a for a in repository.manifest.get('provenance_assets', [])}
    assets.update({a['path']:a for task in tasks for a in task['assets']})
    for relative, asset in assets.items():
        path=(repository.bundle/relative).resolve()
        assert path.is_relative_to(repository.bundle.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==asset['sha256'], relative
    for task in tasks:
        assert service.inspect(task['id'])['loops'], task['id']
        assert service.prompt(task['id'])
    with tempfile.TemporaryDirectory() as temporary:
        temporary=Path(temporary)
        repository.results=temporary/'checks'
        service.trace_sampler.native.cache=temporary/'compiled-traces'
        good=service.check('code2inv-100',['x+y==n and x>=0'])['verifications'][0]
        bad=service.check('code2inv-100',['False'])['verifications'][0]
        assert good['proves_postcondition'] and bad['establishment']!='proved'
        options=TraceSamplingOptions(rows_per_loop=5,samples=1)
        trace=service.sample_traces('code2inv-100',options)
        assert trace['status']=='sampled_all_loops'
        for mode in ['syntax-only','trace-only','syntax-plus-trace']:
            assert service.prompt('code2inv-100',input_mode=mode,trace_record=trace)
        result=dict(tasks=42,verified_assets=len(assets),integer_checks='passed',trace_modes=3,model_calls=0)
        if native:
            check=service.check('svcomp-loop-invariants-eq1',{'0':'w==x && y==z'},timeout=60)
            assert check['native_verification']['status']=='contracts_and_safety_proved',check
            trace=service.sample_traces('svcomp-loop-invariants-eq1',options)
            assert trace['status']=='sampled_all_loops',trace
            result['native_contract_and_trace']='passed'
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,help='Explicit benchmark bundle for an isolated installation check.')
    parser.add_argument('--native',action='store_true',help='Require CBMC/GOTO tools and the native trace compiler.')
    args=parser.parse_args()
    print(json.dumps(verify(args.bundle,args.native),indent=2))
