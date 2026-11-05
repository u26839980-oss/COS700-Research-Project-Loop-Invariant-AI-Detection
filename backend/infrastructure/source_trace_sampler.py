"""Source-specific samplers plus an explicit public-evidence whitelist."""
from dataclasses import asdict
import hashlib

from backend.infrastructure.code2inv_trace_sampler import Code2InvTraceSampler
from backend.infrastructure.native_trace_sampler import NativeTraceSampler
from backend.domain.source_traces import trace_evidence_digest


class SourceTraceSampler:
    def __init__(self, native=None, integer=None):
        self.native = native if native is not None else NativeTraceSampler()
        self.integer = integer if integer is not None else Code2InvTraceSampler()

    def sample(self, *, task, source, template, options, output):
        match task["collection"]:
            case "Code2Inv":
                raw = self.integer.sample(template, options)
                loop_count = 1
                semantics = "Mathematical-integer source SMT relation states. These are not native C iterations or termination evidence."
                types = {}
            case "SV-COMP":
                raw = self.native.sample(source, options, output / "native")
                loop_count = raw["loop_count"]
                semantics = "Concrete ILP32 C loop-head states before guard evaluation; visit gaps are not single iterations. Array snapshots may be partial."
                types = raw["types"]
            case _:
                raise ValueError("Unknown source collection")
        observed = {row["loop_index"] for run in raw["runs"] for row in run["observations"]}
        names = {name for run in raw["runs"] for row in run["observations"] for name in row["state"]}
        # Deliberately omit source names/paths, guards, assertions, verdicts, runtime
        # assertion outcomes, solver feedback, certificates and backend commands.
        evidence = dict(schema="source_observations_v1", semantics=semantics,
            loops=[dict(index=str(i)) for i in range(loop_count)],
            types={name: types.get(name, "mathematical integer") for name in sorted(names)},
            runs=[dict(sample_index=run["sample_index"], exhaustive=False,
                       observations=run["observations"]) for run in raw["runs"]],
            unobserved_loops=[str(i) for i in range(loop_count) if str(i) not in observed])
        return dict(kind="source_trace_sampling", task_id=task["id"], source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            options=asdict(options), model_calls=0, raw=raw, evidence=evidence, evidence_sha256=trace_evidence_digest(evidence),
            status="sampled_all_loops" if len(observed) == loop_count else "incomplete_loop_coverage")
