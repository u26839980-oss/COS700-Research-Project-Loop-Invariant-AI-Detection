"""Check increasing trace budgets do not change inputs or invent intermediate states."""
import argparse
import hashlib
import json
from pathlib import Path


def compare(smaller, larger):
    small_summary = json.loads((smaller / "summary.json").read_text())
    large_summary = json.loads((larger / "summary.json").read_text())
    small_options, large_options = small_summary["options"].copy(), large_summary["options"].copy()
    small_budget = small_options.pop("rows_per_loop")
    large_budget = large_options.pop("rows_per_loop")
    if small_options != large_options or small_budget >= large_budget:
        raise ValueError("Expected identical settings except for an increased observation budget")
    if small_summary["source_sha256"] != large_summary["source_sha256"] or small_summary["source_drift"] or large_summary["source_drift"]:
        raise ValueError("Trace audits have source drift or different implementation versions")
    large_tasks = {row["task_id"]: row for row in large_summary["tasks"]}
    results = []
    for small_task in small_summary["tasks"]:
        large_task = large_tasks[small_task["task_id"]]
        a = json.loads(Path(small_task["log_path"]).read_text())
        b = json.loads(Path(large_task["log_path"]).read_text())
        retained = inputs_unchanged = True
        for left, right in zip(a["raw"]["runs"], b["raw"]["runs"], strict=True):
            if left["status"] == "sampling_timeout" or right["status"] == "sampling_timeout":
                inputs_unchanged = False  # Wall-clock-censored executions are not a verified paired run.
            right_rows = {(row["loop_index"], row["visit"]): row for row in right["observations"]}
            retained &= all(right_rows.get((row["loop_index"], row["visit"])) == row for row in left["observations"])
            if a["raw"]["backend"] == "native_C_ILP32":
                inputs_unchanged &= left["terminal"] == right["terminal"] and left["returncode"] == right["returncode"]
            elif left["observations"] and right["observations"]:
                inputs_unchanged &= left["observations"][0] == right["observations"][0]
        results.append(dict(task_id=small_task["task_id"], smaller_rows_retained=retained, inputs_unchanged=inputs_unchanged))
    return dict(model_calls=0, task_count=len(results), smaller_budget=small_budget, larger_budget=large_budget,
        all_passed=all(row["smaller_rows_retained"] and row["inputs_unchanged"] for row in results), tasks=results,
        comparator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        audit_sha256={str(path): hashlib.sha256((path / "summary.json").read_bytes()).hexdigest() for path in (smaller, larger)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("smaller", type=Path)
    parser.add_argument("larger", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(args.smaller, args.larger)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "tasks"}))
    if not result["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
