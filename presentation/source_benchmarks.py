"""Source benchmark inspection, manual checking and no-execution study planning."""
import json
from dataclasses import asdict

import streamlit as st

from backend.application.source_experiment_plan import make_plan
from backend.domain.source_traces import TraceSamplingOptions


def render_source_traces(record):
    if not record:
        st.info("No trace sample for these settings.")
        return
    st.write(record["status"].replace("_", " ").capitalize())
    evidence = record["evidence"]
    sample_index = st.selectbox("Trace sample", range(len(evidence["runs"])))
    loop_index = st.selectbox("Observed loop", [loop["index"] for loop in evidence["loops"]])
    run = evidence["runs"][sample_index]
    rows = [dict(visit=row["visit"], **row["state"]) for row in run["observations"] if row["loop_index"] == loop_index]
    if rows:
        import pandas as pd
        frame = pd.DataFrame(rows)
        st.dataframe(frame, hide_index=True)
        numeric = [name for name in frame.columns if name != "visit" and all(type(value) is int for value in frame[name])]
        if numeric:
            st.line_chart(frame.set_index("visit")[numeric])
    else:
        st.warning("This sample did not reach this loop.")
    with st.expander("Sampling limits and raw diagnostics"):
        st.json(dict(options=record["options"], runs=[dict(sample_index=item["sample_index"], status=item["status"],
            rows=len(item["observations"])) for item in record["raw"]["runs"]],
            sampling_policy=record["raw"]["sampling_policy"]))
    with st.expander("Exact trace evidence available to models"):
        st.json(evidence)
    st.caption(record["log_path"])


def render_source_result(result):
    st.subheader(result["status"].replace("_", " ").capitalize())
    for verification in result.get("verifications", []):
        with st.expander(verification["candidate"] + ": " + verification["assessment"]):
            st.json(verification)
    native = result.get("native_verification", {})
    if native:
        columns = st.columns(2)
        columns[0].metric("Supplied contracts proved", "Yes" if native.get("invariant_proved") else "Not established")
        columns[1].metric("Complete safety proof", "Yes" if native.get("accepted") else "Not established")
    principal, other = [], []
    for prop in native.get("properties", []):
        if "loop invariant" in prop.get("description", "").lower() or "reach_error" in prop.get("property", ""):
            principal.append(prop)
        else:
            other.append(prop)
    for prop in principal:
        with st.expander(prop.get("description", prop["property"]) + ": " + prop["status"]):
            st.write(prop.get("description", ""))
            st.json(prop)
    if other:
        with st.expander("Frame, native safety and completeness checks"):
            st.json(other)
    diagnostic = native.get("diagnostic") or result.get("diagnostic")
    if diagnostic:
        st.error(diagnostic)
    if result.get("generation"):
        with st.expander("Recorded prompt and raw output"):
            st.json(result["generation"])
    st.caption(result["log_path"])


def render_source_benchmarks(service):
    tasks = service.repository.catalog()
    with st.sidebar:
        collection = st.selectbox("Source collection", ["Code2Inv", "SV-COMP"])
        ids = [task["id"] for task in tasks if task["collection"] == collection]
        selected = st.selectbox("Source task", ids)
        unwind = int(st.number_input("Checker unwinding budget", 1, 1000000, 20, 5))
        timeout = int(st.number_input("Checker timeout (seconds)", 1, 3600, 30, 5))
        input_mode = st.radio("Source input mode", ["syntax-only", "trace-only", "syntax-plus-trace"])
        rows = int(st.number_input("Observations per loop / sample", 1, 500, 5, 5))
        samples = int(st.number_input("Input samples", 1, 100, 3))
        trace_seed = int(st.number_input("Trace seed", 0, 2147483647, 0))
        execution_steps = int(st.number_input("Concrete loop-head execution cap", 1, 10000000, 500000, 5000))
        trace_timeout = int(st.number_input("Sampling timeout per input (seconds)", 1, 3600, 10))
    options = TraceSamplingOptions(rows_per_loop=rows, samples=samples, seed=trace_seed,
        execution_steps=execution_steps, timeout_seconds=trace_timeout)
    info = service.inspect(selected)
    st.subheader(selected)
    st.caption(info["task"]["semantics"])
    if st.button("Sample traces (no models)"):
        with st.spinner("Sampling source executions"):
            sampled = service.sample_traces(selected, options)
        st.session_state["source_traces"] = dict(task=selected, options=asdict(options), record=sampled)
    saved_trace = st.session_state.get("source_traces")
    trace_record = saved_trace["record"] if saved_trace and saved_trace["task"] == selected and saved_trace["options"] == asdict(options) else None
    trace_ready = input_mode == "syntax-only" or bool(trace_record and trace_record["status"] == "sampled_all_loops")
    tabs = st.tabs(["Program and Hypotheses", "Traces", "Provenance", "Experiment Plan"])
    with tabs[0]:
        st.code(info["prepared"], language="c")
        if collection == "Code2Inv":
            candidate = st.text_area("Candidate invariants", key="source_candidate_" + selected, height=120)
            candidates = [line.strip() for line in candidate.splitlines() if line.strip()]
            can_check = bool(candidates)
        else:
            candidates = {}
            for loop in info["loops"]:
                candidates[loop["index"]] = st.text_area(
                    f"Loop {loop['index']}: {loop['guard']}", key=f"source_contract_{selected}_{loop['index']}", height=80)
            can_check = service.native_checker.available()
            if not can_check:
                st.error("Native checker unavailable. Configure CBMC_BIN.")
        if st.button("Check hypotheses" if collection == "Code2Inv" else "Check C program / contracts", disabled=not can_check):
            with st.spinner("Checking"):
                result = service.check(selected, candidates, unwind=unwind, timeout=timeout)
            st.session_state["source_check"] = dict(task=selected, candidates=candidates, unwind=unwind, timeout=timeout, result=result)
        latest = st.session_state.get("source_check")
        if latest and latest["task"] == selected and latest["candidates"] == candidates and latest["unwind"] == unwind and latest["timeout"] == timeout:
            render_source_result(latest["result"])
        with st.expander("Generation prompt"):
            addition = st.text_area("Additional source prompt instructions", key="source_prompt_addition")
            if trace_ready:
                st.code(service.prompt(selected, addition, input_mode=input_mode, trace_record=trace_record), language="text")
            else:
                st.warning("Sample traces covering every loop for the current settings first.")
        generation_configuration = (selected, input_mode, trace_record["evidence_sha256"] if input_mode != "syntax-only" and trace_record else "", addition, unwind, timeout)
        if st.checkbox("Enable local model generation", value=False):
            model = st.text_input("Local model identifier", key="source_generation_model")
            temperature = st.slider("Source model temperature", 0.0, 1.0, 0.0, 0.1)
            seed = int(st.number_input("Source generation seed", 0, 1000000, 0))
            tokens = int(st.number_input("Source output token budget", 128, 16384, 2048, 128))
            context = int(st.number_input("Source context budget", 4096, 262144, 8192, 4096))
            generation_configuration += (model, temperature, seed, tokens, context)
            context_ready = False
            if trace_ready:
                preview = service.prompt(selected, addition, input_mode=input_mode, trace_record=trace_record)
                required = service.conservative_context_requirement(preview, tokens)
                context_ready = context >= required
                if not context_ready:
                    st.warning(f"Conservative byte-based preflight requires context >= {required}. Increase context or reduce sampled evidence; exact token counts depend on the model.")
            if st.button("Generate and check source hypotheses", disabled=not model.strip() or not trace_ready or not context_ready):
                from bootstrap import build_source_model_adapter
                with st.spinner("Generating and checking"):
                    result = service.generate(selected, model.strip(), build_source_model_adapter(), addition=addition,
                        unwind=unwind, timeout=timeout, temperature=temperature, seed=seed,
                        max_output_tokens=tokens, context_length=context, timeout_seconds=600,
                        input_mode=input_mode, trace_record=trace_record)
                st.session_state["source_generation"] = dict(configuration=generation_configuration, result=result)
        generated = st.session_state.get("source_generation")
        if isinstance(generated, dict) and generated["configuration"] == generation_configuration:
            render_source_result(generated["result"])
    with tabs[1]:
        render_source_traces(trace_record)
    with tabs[2]:
        for asset in info["task"]["assets"]:
            st.markdown(f"[{asset['upstream_path']}]({asset['url']})")
        st.json(info["task"])
    with tabs[3]:
        selected_tasks = st.multiselect("Experiment tasks", [task["id"] for task in tasks], default=[task["id"] for task in tasks])
        model_text = st.text_area("Planned model identifiers (one per line)", key="source_plan_models")
        repeats = int(st.number_input("Repetitions per task / treatment / model", 1, 1000, 5))
        modes = st.multiselect("Planned input modes", ["syntax-only", "trace-only", "syntax-plus-trace"], default=["syntax-only"])
        start = int(st.number_input("Trace budget start", 1, 10000, 5, 5))
        stop = int(st.number_input("Trace budget stop", start, 10000, max(start, 25), 5))
        step = int(st.number_input("Trace budget increment", 1, 1000, 5))
        if st.button("Build plan (no model calls)"):
            if not model_text.strip():
                st.error("Enter the model identifiers to compare.")
            else:
                plan = make_plan(selected_tasks, model_text.splitlines(), repeats=repeats, modes=modes,
                                 trace_budgets=range(start, stop + 1, step))
                folder = service.repository.reserve()
                path = service.repository.save(folder, plan)
                st.session_state["source_plan"] = (plan, path)
        if "source_plan" in st.session_state:
            plan, path = st.session_state["source_plan"]
            st.metric("Planned calls (not executed)", plan["planned_generation_calls"])
            st.metric("Distinct programs", plan["distinct_tasks"])
            st.json(plan)
            st.caption(path)
