import re
import pandas as pd
import streamlit as st
from presentation.assessment_labels import assessment_label


def render_generation_warnings(results) -> None:
    for result in results:
        record = result if isinstance(result, dict) else vars(result)
        if (record.get("generation_metadata") or {}).get("response_echo_detected"):
            st.warning(f"{record['model_name']}: echoed prompt text was excluded from hypothesis checks.")
        raw = str(record.get("raw_output", ""))
        if raw.startswith("MODEL_ERROR:"):
            st.error(f"{record['model_name']}: {raw.removeprefix('MODEL_ERROR:').strip()}")


def render_book_hypothesis_results(
    benchmark: dict[str, str],
    results: list[dict[str, object]],
    reference_verification=None,
) -> None:
    results = [result if isinstance(result, dict) else vars(result) for result in results]
    render_generation_warnings(results)
    expected = benchmark.get("invariant") or benchmark.get("book_invariant", "")
    benchmark_complete = bool(benchmark.get("benchmark_complete", False))
    st.subheader("Benchmark Hypothesis Results")
    if reference_verification and reference_verification.verification_contract.startswith("textbook_context_"):
        st.caption("Context-assisted verification: displayed input assumptions and bounds are checked jointly with the model candidate. This is not standalone invariant generation.")
    if benchmark_complete:
        st.caption("Textual reference matches are descriptive only. Z3 status comes from the supplied program and specification; unsupported features are not scored as proofs.")
    else:
        st.caption("This example is incomplete in the provided photos, so hypotheses are shown without scoring.")
    st.markdown("**Expected invariant**")
    st.code(expected)
    if reference_verification is not None:
        with st.expander("Book assumptions and verification contract"):
            st.write(benchmark.get("proof_scope", ""))
            st.json(reference_verification.proof_context)
        with st.expander("Reference validation (not AI output)"):
            st.caption("This baseline checks the stored reference against the displayed program and specification. It is not supplied to the model or counted as an LLM success.")
            st.dataframe(pd.DataFrame([reference_verification.to_row()]), hide_index=True)
            if reference_verification.error:
                st.warning(reference_verification.error)

    rows = []
    for result in results:
        verifications = result.get("verifications") or []
        for verification in verifications:
            rows.append(
                {
                    "model": result["model_name"],
                    "candidate": verification.candidate,
                    "match": "yes" if book_invariant_matches(verification.candidate, expected) else "no",
                    "formal status": assessment_label(verification),
                    "outcome": verification.outcome_kind,
                    "checker limitation scope": verification.limitation_scope,
                    "inductive invariant": "yes" if verification.is_inductive else "not established",
                    "establishment": verification.establishment,
                    "preservation": verification.preservation,
                    "postcondition": verification.postcondition,
                    "error": verification.error,
                }
            )

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.warning("The selected model did not return parseable hypotheses.")

    selected_key = "book_hypothesis_selected_index"
    if selected_key not in st.session_state:
        st.session_state[selected_key] = 0

    flattened = [
        (result, verification)
        for result in results
        for verification in (result.get("verifications") or [])
    ]
    if flattened:
        st.caption("Click a hypothesis index to load its full details.")
        for index, (result, verification) in enumerate(flattened):
            match = "MATCH" if book_invariant_matches(verification.candidate, expected) else "NO MATCH"
            z3_status = assessment_label(verification).upper()
            label = f"{index + 1}. {match} | {z3_status} | {verification.candidate} | {result['model_name']}"
            if st.button(label, key=f"book_select_{index}", use_container_width=True):
                st.session_state[selected_key] = index

        selected_index = min(st.session_state[selected_key], len(flattened) - 1)
        result, verification = flattened[selected_index]
        st.markdown(f"**Hypothesis {selected_index + 1} | {result['model_name']}**")
        st.code(verification.candidate)
        st.metric("Textual reference match", "yes" if book_invariant_matches(verification.candidate, expected) else "no")
        st.metric("Full postcondition proof", "yes" if verification.proves_postcondition else (
            "not established" if verification.outcome_kind in {"checker_limitation", "solver_inconclusive", "checker_error"} else "no"))
        st.metric("Inductive invariant", "proved" if verification.is_inductive else "not established")
        if verification.effective_candidate:
            with st.expander("Candidate together with supplied proof context"):
                st.code(verification.effective_candidate)
        if verification.error:
            st.warning(verification.error)
        if verification.counterexamples:
            with st.expander("Counterexample scalar states"):
                st.json(verification.counterexamples)
        st.dataframe(pd.DataFrame([verification.to_row()]), use_container_width=True, hide_index=True)
        with st.expander("Hoare / Z3 steps", expanded=True):
            for step in verification.hoare_steps:
                st.write(step)

    with st.expander("Raw model outputs"):
        for result in results:
            st.markdown(f"**{result['model_name']}**")
            st.code(str(result["raw_output"]) or "(empty response)")

    with st.expander("Prompt sent to model"):
        for result in results:
            if result.get("prompt"):
                st.markdown(f"**{result['model_name']}**")
                st.code(str(result["prompt"]))


def book_invariant_matches(candidate: str, expected: str) -> bool:
    return normalise_book_formula(candidate) == normalise_book_formula(expected)


def normalise_book_formula(formula: str) -> str:
    formula = formula.lower().strip()
    formula = re.sub(r"^inv\s*=\s*", "", formula)
    formula = re.sub(r"^p'\(i\)\s*=\s*", "", formula)
    formula = re.sub(r"\s+", "", formula)
    formula = formula.replace("¬", "not")
    formula = formula.replace("=>", "implies")
    formula = formula.replace("⇒", "implies")
    return formula


def render_known_benchmark_panel(
    benchmark_name: str,
    benchmark: dict[str, str],
    core_result,
) -> None:
    st.subheader("Known Benchmark Target")
    st.caption(
        "This expected invariant is used as the controlled answer for the selected ready-made example."
    )
    st.markdown(f"**Example:** {benchmark_name}")
    st.markdown(f"**Book source:** {benchmark.get('source', 'No source recorded.')}")
    st.info(benchmark.get("source_status", "No source status recorded."))
    st.code(benchmark["invariant"])

    matching_candidates = []
    for model_result in core_result.model_results:
        for verification in model_result.verifications:
            if _normalise_formula(verification.candidate) == _normalise_formula(benchmark["invariant"]):
                matching_candidates.append(verification.candidate)

    if matching_candidates:
        st.success("At least one trace-derived hypothesis exactly matches the known invariant.")
    else:
        st.info("No trace-derived hypothesis exactly matches the known invariant yet.")


def render_known_match_summary(
    benchmark: dict[str, str],
    model_results,
    source_label: str,
) -> None:
    matches = []
    for model_result in model_results:
        for verification in model_result.verifications:
            if _normalise_formula(verification.candidate) == _normalise_formula(benchmark["invariant"]):
                matches.append(model_result.model_name)

    if matches:
        st.success(f"{source_label} hypotheses matched the known invariant: {', '.join(matches)}")
    else:
        st.info(f"{source_label} hypotheses did not exactly match the known invariant.")


def _normalise_formula(formula: str) -> str:
    return "".join(formula.lower().split())


def render_hypothesis_selector(title: str, model_results, key: str) -> None:
    render_generation_warnings(model_results)
    verifications = []
    for model_result in model_results:
        for verification in model_result.verifications:
            verifications.append((model_result, verification))

    if not verifications:
        st.warning("No hypotheses were available to display.")
        return

    selected_key = f"{key}_selected_index"
    if selected_key not in st.session_state:
        st.session_state[selected_key] = 0

    st.caption("Click a hypothesis index to load its full details.")
    for index, (model_result, verification) in enumerate(verifications):
        status = assessment_label(verification)
        label = (
            f"{index + 1}. {status.upper()} | "
            f"{verification.candidate} | "
            f"{model_result.model_name}"
        )
        if st.button(label, key=f"{key}_select_{index}", use_container_width=True):
            st.session_state[selected_key] = index

    selected_index = st.session_state[selected_key]
    if selected_index >= len(verifications):
        selected_index = 0
        st.session_state[selected_key] = 0

    model_result, verification = verifications[selected_index]
    status = assessment_label(verification)
    st.subheader(title)
    st.markdown(f"**Hypothesis {selected_index + 1} | {model_result.model_name} | {status}**")
    st.code(verification.candidate)
    origin = model_result.generation_metadata.get("candidate_origins", {}).get(verification.candidate)
    if origin == "composed_inductive_clauses":
        st.caption("Composed from model-generated clauses that each passed establishment and preservation; not a complete formula returned directly by the model.")

    summary_columns = st.columns(3)
    summary_columns[0].metric("Establishment", verification.establishment)
    summary_columns[1].metric("Preservation", verification.preservation)
    summary_columns[2].metric("Postcondition", verification.postcondition)
    st.metric("Inductive invariant", "proved" if verification.is_inductive else "not established")

    if verification.inductive_but_insufficient:
        st.info("This is an inductive invariant, but it is too weak to prove the requested postcondition. It is not counted as a successful proof.")

    if verification.error:
        st.error(verification.error)

    if verification.counterexamples:
        with st.expander("Counterexample states"):
            st.json(verification.counterexamples)

    st.markdown("Provenance and checker explanation")
    preliminary = model_result.generation_metadata.get("preliminary")
    if preliminary:
        with st.expander("Model-generated observations (unverified)"):
            st.write(preliminary["response"])
            st.code(preliminary["prompt"])
    st.write(
        getattr(verification, "reason", "")
        or "No explicit reasoning was recorded for this hypothesis."
    )

    st.markdown("Hoare-style evaluation steps")
    hoare_steps = getattr(verification, "hoare_steps", [])
    if hoare_steps:
        for index, step in enumerate(hoare_steps, start=1):
            st.write(f"{index}. {step}")
    else:
        st.write("No Hoare-step trace was recorded for this hypothesis.")
