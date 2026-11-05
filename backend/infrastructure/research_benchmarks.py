from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = {
    "transfer": {
        "name": "Countdown Transfer", "file": "countdown.py",
        "invariant": "x + y == n and x >= 0", "postcondition": "y == n",
    },
    "even_counter": {
        "name": "Even Counter", "file": "even_counter.py",
        "invariant": "2 * even == i and 0 <= i and i <= n + 1",
        "postcondition": "2 * even == i and n <= i and i < n + 2",
    },
    "sum_to_n": {
        "name": "Sum To N", "file": "sum_to_n.py",
        "invariant": "2 * s == i * (i + 1) and 0 <= i and i <= n",
        "postcondition": "2 * s == n * (n + 1)",
    },
}


def scaling_case(template: str, bound: int, bound_mode: str = "constant") -> dict:
    if bound < 0:
        raise ValueError("These nonnegative numerical benchmarks require bounds >= 0.")
    spec = TEMPLATES[template]
    path = ROOT / "examples" / spec["file"]
    body = path.read_text(encoding="utf-8").splitlines()[1:]
    program = "\n".join(([f"n = {bound}"] if bound_mode == "constant" else []) + body)
    return {
        "case_id": f"{template}-{bound_mode}-{bound}",
        "experiment_name": "numerical_bound_sweep_v2",
        "example_name": f"{spec['name']} bound {bound}", "example_template": template,
        "source": f"Local prototype/examples/{spec['file']}",
        "source_status": "Local numerical example, not attributed to the textbook or a paper.",
        "bound_mode": bound_mode, "bound_value": bound, "benchmark_complete": "yes",
        "book_benchmark": "no", "program_text": program, "precondition": "n >= 0",
        "postcondition": spec["postcondition"], "expected_invariant": spec["invariant"],
        "initial_environment": {"n": bound},
    }
