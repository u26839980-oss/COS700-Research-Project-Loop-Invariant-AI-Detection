from pathlib import Path
import json
import pandas as pd
import streamlit as st
from backend.infrastructure.research_records import read_records


def render_research_results(path: Path, rows: pd.DataFrame) -> None:
    from backend.application.research_metrics import summarize

    if st.button("Refresh results"):
        st.rerun()
    metadata_path = path.with_name(f"{path.stem}_metadata.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}
    trials = []
    ledger = path.with_name(f"{path.stem}_trials.jsonl")
    if ledger.exists():
        try:
            trials, partial_tail = read_records(ledger, allow_partial_tail=metadata.get("status") == "running")
            if partial_tail:
                st.warning("The last live record is incomplete and has not been counted yet.")
        except ValueError as exc:
            st.error(str(exc))
            return
    st.write(f"Status: {metadata.get('status', 'starting')} | Completed trials: {len(trials)}/{metadata.get('planned_trials', '?')}")
    if not trials:
        st.info("Waiting for the first completed trial.")
        return
    summary = pd.DataFrame(summarize(trials))
    summary = summary[summary["trials"] > 0]
    if summary.empty:
        st.warning("This run contains only unsupported cases; no model success rate is calculated.")
        st.dataframe(rows, hide_index=True)
        return
    contracts = sorted(summary["verification_contract"].unique())
    st.caption("Verification contract: " + ", ".join(contracts))
    if any(contract.startswith("textbook_context_") for contract in contracts):
        st.caption("Textbook trials include disclosed input assumptions and domain bounds. Context-only answers are not credited as generated invariants. Compare separately from standalone runs.")
    selected_model = st.multiselect("Models", sorted(summary["model_name"].unique()),
                                    default=sorted(summary["model_name"].unique()))
    selected_loop = st.selectbox("Loop template", sorted(summary["example_template"].unique()))
    selected_bound_mode = st.selectbox("Bound treatment", sorted(summary["bound_mode"].unique()))
    view = summary[(summary["model_name"].isin(selected_model)) &
                   (summary["example_template"] == selected_loop) &
                   (summary["bound_mode"] == selected_bound_mode)].copy()
    tabs = st.tabs(["Consistency by bound", "Assessment evidence", "Hypotheses and prompts", "Run configuration"])
    with tabs[0]:
        measured = int(view["inductive_assessed_trials"].sum())
        columns = st.columns(2)
        columns[0].metric("Trials with an inductive invariant",
                          f"{int(view['inductive_trials'].sum())}/{measured}" if measured else "Not recorded")
        columns[1].metric("Trials with a full postcondition proof",
                          f"{int(view['full_proof_trials'].sum())}/{int(view['trials'].sum())}")
        st.metric("Trials limited by checker or solver", int(view["checker_limited_trials"].sum()))
        st.metric("Trials with refuted candidates", int(view["candidate_refuted_trials"].sum()))
        metric_column = "inductive_rate" if measured else "full_proof_rate"
        if measured:
            outcome = st.selectbox("Chart outcome", ["Inductive invariant rate", "Full proof rate"])
            metric_column = "inductive_rate" if outcome == "Inductive invariant rate" else "full_proof_rate"
        else:
            st.caption("This historical ledger did not record inductiveness separately. Missing values are not zero results.")
        st.dataframe(view, hide_index=True, use_container_width=True)
        if not view.empty and selected_bound_mode != "fixed":
            view["bound_value"] = pd.to_numeric(view["bound_value"])
            view["series"] = (view["model_name"] + " | " + view["input_mode"] + " | " +
                              view["verification_contract"] + " | " + view["generation_contract"])
            chart = view.pivot(index="bound_value", columns="series", values=metric_column).sort_index()
            st.line_chart(chart)
    with tabs[1]:
        filtered = rows[(rows["model_name"].isin(selected_model)) &
                        (rows["example_template"] == selected_loop) &
                        (rows["bound_mode"] == selected_bound_mode)]
        attempts = filtered.drop_duplicates("attempt_id")
        counts = attempts.groupby(["model_name", "input_mode", "bound_value", "failure_category"]).size().reset_index(name="attempts")
        st.dataframe(counts, hide_index=True, use_container_width=True)
        st.dataframe(filtered[["model_name", "input_mode", "bound_value", "candidate", "establishment",
                               "preservation", "postcondition_result", "counterexamples", "error"]],
                     hide_index=True, use_container_width=True)
    with tabs[2]:
        if not rows.empty:
            attempt_ids = list(rows["attempt_id"].drop_duplicates())
            selected = st.selectbox("Generation attempt", attempt_ids)
            attempt = rows[rows["attempt_id"] == selected]
            first = attempt.iloc[0]
            st.code(str(first["prompt"]))
            st.code(str(first["raw_output"]))
            try:
                st.json(json.loads(str(first["generation_metadata"])))
            except (ValueError, TypeError):
                pass
            st.dataframe(attempt[["candidate", "accepted", "establishment", "preservation", "postcondition_result"]], hide_index=True)
            st.write(f"Captured trace: {first['trace_file']}")
    with tabs[3]:
        st.json(metadata)


def render_experiment_results() -> None:
    import os
    history = os.getenv("LOOPS_HISTORY_DIR")
    if history and st.radio("Result collection", ["New experiment runs", "Historical research evidence"], horizontal=True) == "Historical research evidence":
        from presentation.archive_browser import render_history
        render_history(history)
        return
    st.subheader("Experiment Results")
    results_root = Path(__file__).resolve().parents[1] / "results"
    result_files = sorted(
        [
            path
            for folder in ("research_experiments", "scaling_experiments")
            for path in (results_root / folder).glob("run_*.csv")
            if not path.name.endswith("_summary.csv") and path.stat().st_size > 0
        ],
        key=lambda path: (path.parent.name == "research_experiments", path.name),
        reverse=True,
    )

    if not result_files:
        st.info("No experiment runs were found yet.")
        st.code(
            "python prototype\\scripts\\run_scaling_experiment.py --suite all",
            language="powershell",
        )
        return

    selected_file = st.selectbox(
        "Experiment run",
        result_files,
        index=0,
        format_func=lambda path: f"{path.parent.name}/{path.name}",
    )
    rows = pd.read_csv(selected_file).fillna("")
    summary_file = selected_file.with_name(f"{selected_file.stem}_summary.csv")

    st.caption(str(selected_file))
    if "protocol_version" in rows.columns:
        render_research_results(selected_file, rows)
        return
    st.warning("Exploratory legacy run: input modes and verification were not fully controlled. Do not combine with protocol v2 results.")
    render_experiment_overview(rows)

    tabs = st.tabs(
        [
            "Input mode comparison",
            "Scaling progression",
            "Solvability factors",
            "Accepted hypotheses",
            "Unsupported examples",
            "Raw rows",
        ]
    )
    with tabs[0]:
        render_input_mode_comparison(rows)
        render_cross_run_comparison(result_files)
    with tabs[1]:
        render_scaling_progression(rows, result_files)
    with tabs[2]:
        render_solvability_factors(rows)
    with tabs[3]:
        render_accepted_hypotheses(rows)
    with tabs[4]:
        render_unsupported_examples(rows)
    with tabs[5]:
        if summary_file.exists():
            with st.expander("Saved summary CSV", expanded=False):
                st.dataframe(pd.read_csv(summary_file).fillna(""), use_container_width=True, hide_index=True)
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_experiment_overview(rows: pd.DataFrame) -> None:
    z3_rows = checked_verification_rows(rows)
    unsupported_rows = rows[rows["verification_backend"] == "unsupported_by_current_z3_subset"]
    accepted_rows = z3_rows[z3_rows["accepted"] == "yes"]
    rejected_rows = z3_rows[z3_rows["accepted"] == "no"]
    solved_cases = count_solved_case_runs(z3_rows)
    total_cases = count_case_runs(z3_rows)

    columns = st.columns(5)
    columns[0].metric("Rows", len(rows))
    columns[1].metric("Z3 checked rows", len(z3_rows))
    columns[2].metric("Accepted rows", len(accepted_rows))
    columns[3].metric("Rejected rows", len(rejected_rows))
    columns[4].metric("Unsupported rows", len(unsupported_rows))

    if total_cases:
        st.metric("Z3-checkable case success", f"{solved_cases}/{total_cases}", f"{solved_cases / total_cases:.1%}")


def render_input_mode_comparison(rows: pd.DataFrame) -> None:
    z3_rows = checked_verification_rows(rows)
    if z3_rows.empty:
        st.info("No Z3-checkable rows are available in this run.")
        return

    records = []
    for input_mode, group in z3_rows.groupby("input_mode"):
        case_count = count_case_runs(group)
        solved_count = count_solved_case_runs(group)
        accepted_count = int((group["accepted"] == "yes").sum())
        rejected_count = int((group["accepted"] == "no").sum())
        records.append(
            {
                "input_mode": input_mode,
                "case_runs": case_count,
                "solved_case_runs": solved_count,
                "success_rate": solved_count / case_count if case_count else 0,
                "accepted_candidates": accepted_count,
                "rejected_candidates": rejected_count,
            }
        )

    comparison = pd.DataFrame(records).sort_values("success_rate", ascending=False)
    st.dataframe(
        comparison.assign(success_rate=comparison["success_rate"].map(lambda value: f"{value:.1%}")),
        use_container_width=True,
        hide_index=True,
    )
    chart_data = comparison.set_index("input_mode")["solved_case_runs"]
    st.bar_chart(chart_data)


def render_cross_run_comparison(result_files: list[Path]) -> None:
    combined_records = []
    for path in result_files:
        run_rows = pd.read_csv(path).fillna("")
        z3_rows = checked_verification_rows(run_rows)
        if z3_rows.empty:
            continue
        for input_mode, group in z3_rows.groupby("input_mode"):
            case_count = count_case_runs(group)
            solved_count = count_solved_case_runs(group)
            combined_records.append(
                {
                    "run": path.stem,
                    "input_mode": input_mode,
                    "case_runs": case_count,
                    "solved_case_runs": solved_count,
                    "success_rate": solved_count / case_count if case_count else 0,
                }
            )

    if not combined_records:
        return

    st.markdown("**Across saved runs**")
    comparison = pd.DataFrame(combined_records)
    st.dataframe(
        comparison.assign(success_rate=comparison["success_rate"].map(lambda value: f"{value:.1%}")),
        use_container_width=True,
        hide_index=True,
    )

    pooled = []
    for input_mode, group in comparison.groupby("input_mode"):
        case_runs = int(group["case_runs"].sum())
        solved = int(group["solved_case_runs"].sum())
        pooled.append(
            {
                "input_mode": input_mode,
                "case_runs": case_runs,
                "solved_case_runs": solved,
                "success_rate": solved / case_runs if case_runs else 0,
            }
        )
    pooled_df = pd.DataFrame(pooled).sort_values("success_rate", ascending=False)
    st.markdown("**Pooled saved-run result**")
    st.dataframe(
        pooled_df.assign(success_rate=pooled_df["success_rate"].map(lambda value: f"{value:.1%}")),
        use_container_width=True,
        hide_index=True,
    )


def render_scaling_progression(rows: pd.DataFrame, result_files: list[Path]) -> None:
    scaling_rows = numeric_scaling_rows(rows)
    if scaling_rows.empty:
        st.info("This run does not contain numeric scaling rows.")
        return

    st.markdown("**Selected run: success by bound**")
    selected_progress = build_scaling_progress_table(scaling_rows)
    st.dataframe(selected_progress, use_container_width=True, hide_index=True)
    render_scaling_chart(selected_progress)

    st.markdown("**Selected run: first solved / first failed bound**")
    st.dataframe(build_scaling_threshold_table(scaling_rows), use_container_width=True, hide_index=True)

    combined = []
    for path in result_files:
        run_rows = pd.read_csv(path).fillna("")
        run_scaling = numeric_scaling_rows(run_rows)
        if run_scaling.empty:
            continue
        run_scaling = run_scaling.copy()
        run_scaling["run"] = path.stem
        combined.append(run_scaling)

    if not combined:
        return

    combined_rows = pd.concat(combined, ignore_index=True)
    st.markdown("**Across saved runs: success count by bound**")
    pooled = build_pooled_scaling_table(combined_rows)
    st.dataframe(pooled, use_container_width=True, hide_index=True)


def numeric_scaling_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if "bound_value" not in rows.columns:
        return pd.DataFrame()
    scaling_rows = rows[
        (rows["verification_backend"].isin(["z3", "benchmark_z3"]))
        & (rows["bound_mode"] != "fixed")
        & (rows["bound_value"].astype(str).str.strip() != "")
    ].copy()
    if scaling_rows.empty:
        return scaling_rows
    scaling_rows["bound_value"] = pd.to_numeric(scaling_rows["bound_value"], errors="coerce")
    scaling_rows = scaling_rows.dropna(subset=["bound_value"])
    scaling_rows["bound_value"] = scaling_rows["bound_value"].astype(int)
    return scaling_rows


def build_scaling_progress_table(scaling_rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for (bound, input_mode), group in scaling_rows.groupby(["bound_value", "input_mode"]):
        accepted = group[group["accepted"] == "yes"]
        records.append(
            {
                "bound": int(bound),
                "input_mode": input_mode,
                "solved": "yes" if not accepted.empty else "no",
                "accepted_candidates": len(accepted),
                "rejected_candidates": int((group["accepted"] == "no").sum()),
                "best_candidate": accepted.iloc[0]["candidate"] if not accepted.empty else "",
            }
        )
    return pd.DataFrame(records).sort_values(["bound", "input_mode"])


def render_scaling_chart(progress: pd.DataFrame) -> None:
    if progress.empty:
        return
    chart_rows = progress.copy()
    chart_rows["solved_value"] = chart_rows["solved"].map({"yes": 1, "no": 0})
    pivot = chart_rows.pivot_table(
        index="bound",
        columns="input_mode",
        values="solved_value",
        aggfunc="max",
        fill_value=0,
    )
    st.line_chart(pivot)


def build_scaling_threshold_table(scaling_rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for input_mode, group in scaling_rows.groupby("input_mode"):
        bounds = sorted(group["bound_value"].unique())
        solved_bounds = sorted(
            group[group["accepted"] == "yes"]["bound_value"].unique()
        )
        failed_bounds = [bound for bound in bounds if bound not in solved_bounds]
        records.append(
            {
                "input_mode": input_mode,
                "bounds_tested": ", ".join(str(bound) for bound in bounds),
                "solved_bounds": ", ".join(str(bound) for bound in solved_bounds) or "none",
                "failed_bounds": ", ".join(str(bound) for bound in failed_bounds) or "none",
                "smallest_solved_bound": solved_bounds[0] if solved_bounds else "",
                "largest_solved_bound": solved_bounds[-1] if solved_bounds else "",
                "success_rate": f"{len(solved_bounds) / len(bounds):.1%}" if bounds else "0.0%",
            }
        )
    return pd.DataFrame(records).sort_values("input_mode")


def build_pooled_scaling_table(combined_rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for (bound, input_mode), group in combined_rows.groupby(["bound_value", "input_mode"]):
        attempts = group[["run", "example_name"]].drop_duplicates()
        solved_attempts = group[group["accepted"] == "yes"][["run", "example_name"]].drop_duplicates()
        records.append(
            {
                "bound": int(bound),
                "input_mode": input_mode,
                "run_case_attempts": len(attempts),
                "solved_run_cases": len(solved_attempts),
                "success_rate": f"{len(solved_attempts) / len(attempts):.1%}" if len(attempts) else "0.0%",
            }
        )
    return pd.DataFrame(records).sort_values(["bound", "input_mode"])


def render_solvability_factors(rows: pd.DataFrame) -> None:
    z3_rows = checked_verification_rows(rows).copy()
    if z3_rows.empty:
        st.info("No Z3-checkable rows are available in this run.")
        return

    z3_rows["uses_trace"] = z3_rows["input_mode"].str.contains("trace", regex=False)
    z3_rows["uses_syntax"] = z3_rows["input_mode"].str.contains("syntax", regex=False)
    z3_rows["has_disjunction"] = z3_rows["candidate"].str.contains(r"\bor\b", case=False, regex=True)
    z3_rows["has_conservation_relation"] = z3_rows["candidate"].str.contains(r"x\s*\+\s*y\s*==\s*n|y\s*\+\s*x\s*==\s*n", case=False, regex=True)
    z3_rows["mentions_nonnegative_bound"] = z3_rows["candidate"].str.contains(r"x\s*>=\s*0|0\s*<=\s*x|y\s*>=\s*0|0\s*<=\s*y", case=False, regex=True)

    factor_records = []
    for factor in ["uses_trace", "uses_syntax", "has_disjunction", "has_conservation_relation", "mentions_nonnegative_bound"]:
        for value, group in z3_rows.groupby(factor):
            factor_records.append(
                {
                    "factor": factor,
                    "value": str(bool(value)),
                    "rows": len(group),
                    "accepted_rows": int((group["accepted"] == "yes").sum()),
                    "acceptance_rate": (group["accepted"] == "yes").sum() / len(group),
                }
            )

    factors = pd.DataFrame(factor_records)
    st.markdown("**Candidate/input factors**")
    st.dataframe(
        factors.assign(acceptance_rate=factors["acceptance_rate"].map(lambda value: f"{value:.1%}")),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Why rejected candidates failed**")
    failed = z3_rows[z3_rows["accepted"] == "no"]
    if failed.empty:
        st.success("No rejected Z3 candidates in this run.")
    else:
        failure_records = []
        for obligation in ["establishment", "preservation", "postcondition_result"]:
            failure_records.append(
                {
                    "failed_obligation": obligation,
                    "count": int((failed[obligation] == "failed").sum()),
                }
            )
        st.dataframe(pd.DataFrame(failure_records), use_container_width=True, hide_index=True)

    st.info(
        "For transfer loops, solvable candidates usually capture a conserved quantity such as `x + y == n` "
        "and a useful bound such as `x >= 0`. Candidates that only restate the postcondition often fail "
        "establishment or preservation."
    )


def render_accepted_hypotheses(rows: pd.DataFrame) -> None:
    accepted = checked_verification_rows(rows)
    accepted = accepted[accepted["accepted"] == "yes"]
    if accepted.empty:
        st.info("No accepted Z3 hypotheses are available in this run.")
        return

    columns = [
        "example_name",
        "input_mode",
        "model_name",
        "candidate",
        "establishment",
        "preservation",
        "postcondition_result",
    ]
    st.dataframe(accepted[columns], use_container_width=True, hide_index=True)


def render_unsupported_examples(rows: pd.DataFrame) -> None:
    unsupported = rows[rows["verification_backend"] == "unsupported_by_current_z3_subset"]
    if unsupported.empty:
        st.success("No unsupported examples in this run.")
        return

    columns = ["example_name", "input_mode", "source", "error"]
    st.dataframe(unsupported[columns], use_container_width=True, hide_index=True)
    st.warning(
        "These examples are not model failures. They are backend coverage gaps in the current parser/Z3 checker."
    )


def count_case_runs(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    return rows["example_name"].astype(str).nunique()


def count_solved_case_runs(rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    solved = rows[rows["accepted"] == "yes"]
    return solved["example_name"].astype(str).nunique()


def checked_verification_rows(rows: pd.DataFrame) -> pd.DataFrame:
    return rows[rows["verification_backend"].isin(["z3", "benchmark_z3"])]
