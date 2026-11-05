from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace

import pandas as pd
import streamlit as st

from bootstrap import build_workbench_service, build_textbook_service
from backend.application.textbook_service import TextbookRequest
from backend.infrastructure.model_adapter import ModelAdapter
from backend.infrastructure.z3_checker import Z3AvailabilityError
from backend.infrastructure.prompt_builder import PromptBuilder
from backend.domain.models import ExecutionTrace


from backend.domain.benchmarks import EXAMPLES
from backend.application.benchmark_guidance import build_guidance_examples
from backend.infrastructure.textbook_prompt_builder import build_book_benchmark_prompt
from backend.infrastructure.candidate_parser import extract_book_candidates
from presentation.configuration import (
    DEFAULT_PROGRAM, DEFAULT_PRECONDITION, DEFAULT_INVARIANT, DEFAULT_POSTCONDITION,
)
from presentation.research_dashboard import render_experiment_results, render_research_results
from presentation.hypotheses import (
    render_book_hypothesis_results, render_known_benchmark_panel,
    render_known_match_summary, render_hypothesis_selector,
)


def main() -> None:
    st.set_page_config(
        page_title="Loop Invariant Workbench",
        page_icon="",
        layout="wide",
    )

    st.title("Loop Invariant Hypothesizer")
    workspace_view = st.sidebar.radio(
        "Workspace",
        ["Hypothesizer", "Source benchmarks", "Experiment results"],
        index=1 if st.query_params.get("workspace") == "sources" else 0,
    )
    if workspace_view == "Experiment results":
        render_experiment_results()
        return
    if workspace_view == "Source benchmarks":
        try:
            from bootstrap import build_source_benchmark_service
            from presentation.source_benchmarks import render_source_benchmarks
            render_source_benchmarks(build_source_benchmark_service())
        except ImportError as exc:
            st.error(f"Source benchmark dependency error: {exc}. Install requirements.txt in the app environment and restart Streamlit.")
        except (ValueError, OSError, KeyError) as exc:
            st.error(str(exc))
        return

    service = build_workbench_service()
    model_adapter = ModelAdapter()

    if "program_text" not in st.session_state:
        st.session_state.program_text = DEFAULT_PROGRAM
        st.session_state.precondition = DEFAULT_PRECONDITION
        st.session_state.invariant = ""
        st.session_state.postcondition = DEFAULT_POSTCONDITION
        st.session_state.selected_example_name = "Countdown Transfer"
        st.session_state.trace_iteration_limit = 25

    with st.sidebar:
        st.header("Examples")
        example_names = list(EXAMPLES.keys())
        selected_example = st.selectbox(
            "Load example",
            example_names,
            index=example_names.index(st.session_state.selected_example_name)
            if st.session_state.get("selected_example_name") in example_names
            else 0,
        )
        if st.button("Use Example"):
            example = EXAMPLES[selected_example]
            st.session_state.program_text = example["program"]
            st.session_state.precondition = example["precondition"]
            st.session_state.invariant = ""
            st.session_state.postcondition = example["postcondition"]
            st.session_state.selected_example_name = selected_example
            st.session_state.trace_iteration_limit = int(
                example.get("trace_iteration_limit", st.session_state.get("trace_iteration_limit", 25))
            )
            st.session_state.latest_analysis = None
            st.session_state.latest_book_analysis = None

        experiment_mode = st.radio(
            "Benchmark mode",
            ["Known benchmark", "Unknown/open example"],
            index=0,
            help=(
                "Known benchmark keeps the expected invariant available for evaluation. "
                "Unknown/open example treats the loop as an unseen case."
            ),
        )
        is_book = bool(EXAMPLES.get(st.session_state.get("selected_example_name", ""), {}).get("book_benchmark"))
        include_prompt_guidance = st.checkbox(
            "Pre-prompt with example shapes",
            value=False,
            disabled=is_book,
            help="Adds simple worked invariant patterns to the local model prompt.",
        )
        clean_evaluation_mode = st.checkbox(
            "Clean evaluation mode",
            value=True,
            disabled=is_book,
            help=(
                "Reduces leakage by removing source labels, example-shape guidance, and benchmark names from prompts. "
                "Scoring still uses the hidden benchmark data."
            ),
        )

        st.header("AI Configuration")
        mode = st.radio(
            "Input mode",
            ["syntax-only", "trace-only", "syntax-plus-trace"],
            index=2,
        )

        installed_models = model_adapter.list_models()
        if not installed_models:
            st.warning("No models available. Configure Ollama or a server-side Gemini API key.")

        selected_models = st.multiselect(
            "Select up to 3 models",
            installed_models,
            default=installed_models[:1],
        )

        if len(selected_models) > 3:
            st.error("Select no more than 3 models.")
            st.stop()

        if any(name.startswith("gemini/") for name in selected_models):
            st.info("Gemini sends the selected prompt to Google's hosted API and requires internet access.")

        sample_limit = st.number_input(
            "Trace iteration limit",
            min_value=1,
            max_value=5000,
            step=1,
            key="trace_iteration_limit",
        )
        temperature = st.slider(
            "Model temperature",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.1,
            help="0 reduces sampling variability; reproducibility also depends on the provider and runtime. Requests do not include earlier conversation history.",
        )

        run_ai = st.checkbox("Generate AI hypotheses", value=True)
        st.caption("Response time depends on the model and server load.")

    st.subheader("LLM Prompt")
    with st.expander("Prompt controls", expanded=False):
        if "frontend_prompt_addition" not in st.session_state:
            st.session_state.frontend_prompt_addition = ""
        prompt_mode = st.radio(
            "Prompt mode",
            ["Minimal prompt + frontend additions"] if is_book else ["Original few-shot prompt", "Minimal prompt + frontend additions", "Guided self-check prompt"],
            index=0,
            help=(
                "Original few-shot uses the existing guided prompt with example shapes. "
                "Minimal keeps only the required output contract and appends your frontend instructions."
            ),
        )
        prompt_style = "guided_self_check" if prompt_mode == "Guided self-check prompt" and not clean_evaluation_mode else (
            "minimal_frontend"
            if clean_evaluation_mode or prompt_mode == "Minimal prompt + frontend additions"
            else "original_few_shot"
        )
        generation_label = st.selectbox(
            "Generation strategy", ["Direct"] if is_book else ["Direct", "Relations-first", "Relations only"],
            help="Relations-first adds a model observation stage. Relations only discovers clauses without seeing the postcondition; the checker still uses it.",
        )
        generation_strategy = {"Direct": "direct", "Relations-first": "relations_first", "Relations only": "relations_only"}[generation_label]
        candidate_label = st.selectbox(
            "Candidate policy", ["Independent"] if is_book else ["Independent", "Conjoin inductive clauses"],
            help="Composition joins only model clauses that each pass establishment and preservation. Composed results are labelled separately.",
        )
        candidate_policy = "conjoin_inductive" if candidate_label == "Conjoin inductive clauses" else "independent"
        max_output_tokens = st.number_input("Output token limit", min_value=128, max_value=4096, value=384, step=128)
        use_repeat_penalty = st.checkbox("Set Ollama repetition penalty", value=False)
        repeat_penalty_value = st.slider("Repetition penalty", min_value=1.0, max_value=2.0, value=1.2, step=0.05, disabled=not use_repeat_penalty)
        repeat_penalty = repeat_penalty_value if use_repeat_penalty else None
        if is_book:
            prompt_preview = build_book_benchmark_prompt("", {}, "<program>", "<precondition>",
                "<postcondition>", "", prompt_style, "", True, [], preview=True)
        else:
            prompt_preview = PromptBuilder().build("syntax-only", {"guard": "...", "body": []},
                ExecutionTrace([]), "<precondition>", "<postcondition>", prompt_style=prompt_style)
        st.text_area(
            "Selected prompt preview",
            value=prompt_preview,
            height=260,
            disabled=True,
            help="Preview of the prompt mode currently selected above.",
        )
        frontend_prompt_addition = st.text_area(
            "Additional prompt instructions",
            height=120,
            key="frontend_prompt_addition",
            placeholder=(
                "Optional. Example: Prefer quantified prefix/suffix invariants. "
                "Explain internally using Hoare-style establishment, preservation, and postcondition reasoning."
            ),
            disabled=prompt_style not in {"minimal_frontend", "guided_self_check"},
            help="Used by the minimal and guided self-check prompts.",
        )

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Program")
        program_text = st.text_area("Loop program", height=260, key="program_text")

    with right:
        st.subheader("Specification")
        precondition = st.text_input("Precondition", key="precondition")
        postcondition = st.text_input("Postcondition", key="postcondition")
        with st.expander("Optional manual baseline"):
            invariant = st.text_input(
                "Manual invariant to compare against AI",
                key="invariant",
                placeholder="Leave blank for pure AI hypothesis generation",
            )

    run_clicked = st.button("Generate Hypotheses", type="primary")
    prompt_refresh_id = ""
    analysis_key = "latest_analysis"
    selected_benchmark_name = st.session_state.get("selected_example_name", selected_example)
    selected_benchmark = EXAMPLES.get(selected_benchmark_name)

    if selected_benchmark and selected_benchmark.get("book_benchmark"):
        scalar_inputs_text = ""
        st.caption(
            f"{selected_benchmark.get('source', '')} "
            "This ready-made example uses the converted Python-style loop shown above."
        )
        st.caption(selected_benchmark.get("conversion_note", ""))
        textbook_inputs_text = st.text_area(
            "Sample inputs (JSON)",
            value=json.dumps(selected_benchmark["sample_inputs"], indent=2),
            key=f"textbook_inputs_{selected_benchmark_name}",
            height=150,
        )
    else:
        textbook_inputs_text = ""
        scalar_inputs_text = st.text_area("Scalar sample inputs (JSON)", value="{}",
            key="scalar_sample_inputs", height=100)
    effective_prompt_guidance = include_prompt_guidance and not clean_evaluation_mode
    book_configuration = (
        program_text, precondition, postcondition, textbook_inputs_text, mode,
        tuple(selected_models), prompt_style, frontend_prompt_addition, temperature,
        max_output_tokens, repeat_penalty, clean_evaluation_mode, effective_prompt_guidance,
        int(sample_limit), invariant, run_ai,
    )

    if run_clicked and selected_benchmark and selected_benchmark.get("book_benchmark"):
        if run_ai and not selected_models:
            st.error("Select at least one model to generate hypotheses.")
            return

        status = st.empty()
        try:
            textbook_inputs = json.loads(textbook_inputs_text)
            if not isinstance(textbook_inputs, dict):
                raise ValueError("Sample inputs must be a JSON object.")
            request = TextbookRequest(
                example_name=selected_benchmark_name, benchmark=selected_benchmark,
                program_text=program_text, precondition=precondition, postcondition=postcondition,
                inputs=textbook_inputs, iteration_limit=int(sample_limit),
                model_names=tuple(selected_models) if run_ai else (),
                input_mode=mode, prompt_style=prompt_style,
                frontend_prompt_addition=frontend_prompt_addition,
                prompt_refresh_id=prompt_refresh_id, clean_evaluation_mode=clean_evaluation_mode,
                guidance_examples=build_guidance_examples(selected_benchmark_name, effective_prompt_guidance),
                manual_invariant=invariant, temperature=temperature,
                max_output_tokens=int(max_output_tokens), repeat_penalty=repeat_penalty,
            )
            book_analysis = build_textbook_service(service.repository).analyse(request, model_adapter, status.info)
        except Exception as exc:
            st.error(str(exc))
            return
        st.session_state.latest_book_analysis = {
            "example_name": selected_benchmark_name, "benchmark": selected_benchmark,
            "results": book_analysis.results, "trace": book_analysis.trace,
            "final_state": book_analysis.final_state,
            "reference_verification": book_analysis.reference_verification,
            "prompt_style": prompt_style, "frontend_prompt_addition": frontend_prompt_addition,
            "temperature": temperature, "clean_evaluation_mode": clean_evaluation_mode,
            "configuration": book_configuration,
        }
        st.session_state.latest_analysis = None
        status.success("Hypothesis generation complete.")

    latest_book_analysis = st.session_state.get("latest_book_analysis")
    if (
        latest_book_analysis
        and latest_book_analysis.get("example_name") == selected_benchmark_name
        and latest_book_analysis.get("prompt_style") == prompt_style
        and latest_book_analysis.get("frontend_prompt_addition") == frontend_prompt_addition
        and latest_book_analysis.get("temperature") == temperature
        and latest_book_analysis.get("clean_evaluation_mode") == clean_evaluation_mode
        and latest_book_analysis.get("configuration") == book_configuration
    ):
        st.subheader("Execution trace")
        trace_frame = pd.DataFrame(latest_book_analysis["trace"].rows)
        st.dataframe(trace_frame, hide_index=True)
        numeric_columns = [column for column in trace_frame.columns if column != "iteration"
                           and all(type(value) is int for value in trace_frame[column])]
        if numeric_columns:
            st.line_chart(trace_frame.set_index("iteration")[numeric_columns])
        with st.expander("Final state after post-loop statements"):
            st.json(latest_book_analysis["final_state"])
        render_book_hypothesis_results(
            latest_book_analysis["benchmark"],
            latest_book_analysis["results"],
            latest_book_analysis.get("reference_verification"),
        )
        return

    if run_clicked:
        if run_ai and not selected_models:
            st.error("Select at least one model to generate hypotheses.")
            return

        status = st.empty()

        try:
            scalar_inputs = json.loads(scalar_inputs_text)
            if not isinstance(scalar_inputs, dict):
                raise ValueError("Scalar sample inputs must be a JSON object.")
            analysis_arguments = {
                "program_text": program_text, "precondition": precondition,
                "postcondition": postcondition, "user_invariant": invariant,
                "iteration_limit": int(sample_limit), "initial_environment": scalar_inputs,
                "progress_callback": status.info,
            }
            model_results = []
            ai_log_path = ""
            if run_ai:
                ai_result = service.analyse(
                    **analysis_arguments,
                    model_names=selected_models, model_mode=mode, model_adapter=model_adapter,
                    guidance_examples=build_guidance_examples(selected_benchmark_name, effective_prompt_guidance),
                    frontend_prompt_addition=frontend_prompt_addition, prompt_style=prompt_style,
                    temperature=temperature, prompt_refresh_id=prompt_refresh_id,
                    generation_strategy=generation_strategy, candidate_policy=candidate_policy,
                    max_output_tokens=int(max_output_tokens), repeat_penalty=repeat_penalty,
                )
                patterns = []
                for result in ai_result.model_results:
                    if result.model_name == "trace-pattern-hypothesizer":
                        patterns.append(result)
                    else:
                        model_results.append(result)
                core_result = replace(ai_result, model_results=patterns)
                ai_log_path = ai_result.log_path
                status.success("Hypothesis generation complete.")
            else:
                core_result = service.analyse_without_models(**analysis_arguments)
                status.success("Trace-derived hypothesis generation complete.")
        except Z3AvailabilityError as exc:
            st.error(str(exc))
            st.code("python -m pip install z3-solver", language="powershell")
            return
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")
            return

        st.session_state[analysis_key] = {
            "core_result": core_result,
            "model_results": model_results,
            "ai_log_path": ai_log_path,
            "run_ai": run_ai,
            "experiment_mode": experiment_mode,
            "selected_benchmark_name": selected_benchmark_name,
            "manual_invariant": invariant,
            "frontend_prompt_addition": frontend_prompt_addition,
            "prompt_style": prompt_style,
            "generation_strategy": generation_strategy,
            "candidate_policy": candidate_policy,
            "max_output_tokens": max_output_tokens,
            "repeat_penalty": repeat_penalty,
            "program_text": program_text,
            "precondition": precondition,
            "postcondition": postcondition,
            "sample_limit": sample_limit,
            "selected_models": selected_models,
            "model_mode": mode,
            "temperature": temperature,
            "prompt_refresh_id": prompt_refresh_id,
            "clean_evaluation_mode": clean_evaluation_mode,
            "scalar_inputs_text": scalar_inputs_text,
        }

    if not st.session_state.get(analysis_key):
        return

    latest_analysis = st.session_state[analysis_key]
    if (
        latest_analysis.get("selected_benchmark_name") != selected_benchmark_name
        or latest_analysis.get("prompt_style") != prompt_style
        or latest_analysis.get("generation_strategy", "direct") != generation_strategy
        or latest_analysis.get("candidate_policy", "independent") != candidate_policy
        or latest_analysis.get("max_output_tokens", max_output_tokens) != max_output_tokens
        or latest_analysis.get("repeat_penalty") != repeat_penalty
        or latest_analysis.get("program_text", program_text) != program_text
        or latest_analysis.get("precondition", precondition) != precondition
        or latest_analysis.get("postcondition", postcondition) != postcondition
        or latest_analysis.get("sample_limit", sample_limit) != sample_limit
        or latest_analysis.get("selected_models", selected_models) != selected_models
        or latest_analysis.get("model_mode", mode) != mode
        or latest_analysis.get("frontend_prompt_addition") != frontend_prompt_addition
        or latest_analysis.get("temperature") != temperature
        or latest_analysis.get("clean_evaluation_mode") != clean_evaluation_mode
        or latest_analysis.get("scalar_inputs_text", "{}") != scalar_inputs_text
    ):
        st.info("Prompt/model settings changed. Click Generate Hypotheses to run the models with the new prompt.")
        return

    core_result = latest_analysis["core_result"]
    model_results = latest_analysis["model_results"]
    ai_log_path = latest_analysis["ai_log_path"]
    run_ai_for_result = latest_analysis["run_ai"]
    experiment_mode_for_result = latest_analysis["experiment_mode"]
    selected_benchmark_name = latest_analysis["selected_benchmark_name"]
    invariant_for_result = latest_analysis["manual_invariant"]

    st.subheader("Execution Trace")
    if core_result.trace.rows:
        trace_df = pd.DataFrame(core_result.trace.rows)
        st.dataframe(trace_df, use_container_width=True)

        numeric_columns = [
            column
            for column in trace_df.columns
            if column != "iteration" and pd.api.types.is_numeric_dtype(trace_df[column])
        ]
        if numeric_columns:
            chart_df = trace_df.set_index("iteration")[numeric_columns]
            st.line_chart(chart_df)
    else:
        st.info("No trace rows were produced.")

    st.subheader("Syntax Features")
    st.json(core_result.syntax_features)

    if invariant_for_result.strip():
        st.subheader("Manual Baseline Check")
        st.dataframe(
            pd.DataFrame([core_result.user_verification.to_row()]),
            use_container_width=True,
            hide_index=True,
        )

    if experiment_mode_for_result == "Known benchmark" and selected_benchmark:
        render_known_benchmark_panel(selected_benchmark_name, selected_benchmark, core_result)

    st.subheader("Trace-Derived Hypotheses")
    trace_rows = []
    for model_result in core_result.model_results:
        for verification in model_result.verifications:
            row = verification.to_row()
            row["model"] = model_result.model_name
            row["mode"] = model_result.mode
            trace_rows.append(row)

    if trace_rows:
        render_hypothesis_selector(
            title="Trace hypothesis details",
            model_results=core_result.model_results,
            key="trace_hypotheses",
        )
    else:
        st.warning("No trace-derived hypotheses were produced.")

    if model_results:
        st.subheader("Generated Hypotheses")
        rows = []
        for model_result in model_results:
            for verification in model_result.verifications:
                row = verification.to_row()
                row["model"] = model_result.model_name
                row["mode"] = model_result.mode
                rows.append(row)
        if rows:
            render_hypothesis_selector(
                title="AI hypothesis details",
                model_results=model_results,
                key="ai_hypotheses",
            )
            if experiment_mode_for_result == "Known benchmark" and selected_benchmark:
                render_known_match_summary(
                    benchmark=selected_benchmark,
                    model_results=model_results,
                    source_label="AI-generated",
                )
        else:
            st.warning("The selected model did not return parseable invariant hypotheses.")
            for result in model_results:
                metadata = result.generation_metadata
                diagnostic = metadata.get("provider_error") or metadata.get("preliminary", {}).get("generation_metadata", {}).get("provider_error")
                if result.raw_output.startswith("MODEL_ERROR:"):
                    st.error(f"{result.model_name}: {diagnostic or result.raw_output}")
                elif metadata.get("done_reason") == "length":
                    st.warning(f"{result.model_name}: output reached the token limit before a complete supported formula was parsed.")

        with st.expander("Raw model outputs"):
            for model_result in model_results:
                st.markdown(f"**{model_result.model_name} ({model_result.mode})**")
                st.code(model_result.raw_output or "(empty response)")
        with st.expander("Prompts sent to models"):
            for model_result in model_results:
                st.markdown(f"**{model_result.model_name} ({model_result.mode})**")
                st.code(model_result.prompt or "(prompt was not recorded)")
    elif run_ai_for_result:
        st.warning("No model results were produced.")

    st.subheader("Experiment Log")
    if run_ai_for_result and ai_log_path:
        st.caption(ai_log_path)
    else:
        st.caption("AI experiment log is written when local model generation is run.")



if __name__ == "__main__":
    main()
