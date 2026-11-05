"""Resource settings shared by source trace samplers, never proof assumptions."""
from dataclasses import dataclass
import hashlib
import json


def trace_evidence_digest(evidence):
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class TraceSamplingOptions:
    rows_per_loop: int = 5
    samples: int = 3
    seed: int = 0
    execution_steps: int = 500000
    timeout_seconds: int = 10
    array_window: int = 8

    def __post_init__(self):
        limits = {"rows_per_loop": (1, 500), "samples": (1, 100), "seed": (0, 2147483647),
                  "execution_steps": (1, 10000000), "timeout_seconds": (1, 3600), "array_window": (1, 32)}
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}].")
