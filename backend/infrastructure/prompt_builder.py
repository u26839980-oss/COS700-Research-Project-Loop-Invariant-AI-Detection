from __future__ import annotations

from itertools import combinations
from typing import Any

from backend.domain.models import ExecutionTrace
from backend.infrastructure.prompt_sections import (
    additional_user_sections, refresh_sections, sample_trace_rows, trace_sections,
)


class PromptBuilder:
    def build(
        self,
        mode: str,
        syntax_features: dict[str, object],
        trace: ExecutionTrace,
        precondition: str,
        postcondition: str,
        guidance_examples: list[dict[str, str]] | None = None,
        frontend_prompt_addition: str = "",
        prompt_style: str = "original_few_shot",
        prompt_refresh_id: str = "",
        include_trace_observations: bool = False,
    ) -> str:
        if prompt_style == "guided_self_check":
            sections = [
                "Task: propose an inductive loop invariant. Prefer one that helps prove the supplied postcondition, but a valid partial invariant is also a useful hypothesis.",
                "The precondition applies before initialization; the invariant applies at the loop head, including the final guard check.",
                "A candidate is ONE COMPLETE Boolean formula. Join all necessary clauses with and; separate lines mean alternative complete candidates, not pieces of one invariant.",
                "Work through these checks before answering:",
                "1. Establishment: evaluate your entire formula after initialization. If traces are provided, it must hold in the first state and every later recorded state.",
                "2. Preservation: assume your entire formula and the guard before an iteration; use the updates to check that the same formula holds afterward. A finite trace alone cannot establish this proof.",
                "3. Goal relevance: assume your formula and the false guard; consider what it proves about the postcondition. Failure to prove the entire postcondition does not invalidate an otherwise inductive invariant.",
                "The postcondition describes the goal at exit, not an assumption that is true during the loop. Reject a copied postcondition if it fails the initial state or an update.",
                "Keep the conditions needed to make the whole argument work together. Do not output the precondition, guard, and postcondition as three separate candidates.",
                "Use only target program/state variables, integer arithmetic, comparisons, and Python and/or/not. The iteration column is an observation index, not a program variable.",
                "Supported expression syntax: integer constants and target variables; +, -, *, //, %, **; ==, !=, <, <=, >, >=; and, or, not; parentheses.",
                "Use == for equality, never assignment =. Do not use floating-point /, function calls, or mathematical notation. For // and %, the divisor must be a positive integer constant; powers must be constant integers from 0 to 16.",
                "A relation can hold throughout execution yet be too weak to prove the goal. Include the necessary range constraints in the SAME formula, rather than listing initial values or exit-only facts.",
                "Return ONE complete candidate, on one line, between CANDIDATES and END. All necessary relations and bounds belong together on that line.",
                "Return only formulas in that format, without explanations or code fences. Do not invent unavailable source code when only traces are supplied.",
            ]
        elif prompt_style == "minimal_frontend":
            sections = [
                "You are helping generate loop invariant hypotheses.",
                "A loop invariant must hold at the initial loop head and be preserved by every loop iteration. Prefer facts relevant to the postcondition; they need not prove the entire goal alone.",
                "Return only candidate invariants between CANDIDATES and END.",
                "Return at most three candidate invariants.",
                "Put one invariant per line.",
                "Use Python-style boolean syntax: and, or, not.",
                "Do not return only the postcondition unless it is true before the loop and preserved by every iteration.",
                "Do not return JSON, labels, bullet points, code blocks, or explanations.",
            ]
        else:
            sections = [
                "You are helping generate loop invariant hypotheses.",
                "Think about the loop before proposing candidates.",
                "A loop invariant is a fact that holds at the initial loop head and remains true after every loop iteration. Prefer facts that help prove the postcondition, even if they are insufficient alone.",
                "Generate inductive invariants, not only facts observed in the trace.",
                "Use these invariant-shape rules:",
                "- Transfer loops usually preserve a conservation relation between variables, because one value decreases while another increases.",
                "- Counter loops usually need bounds, for example the counter starts inside a range and moves toward the loop limit.",
                "- Summation loops usually connect the accumulator to the counter using the closed form for the amount accumulated so far.",
                "- Repeated-addition loops usually connect the accumulator, the fixed step value, and the number of completed iterations.",
                "- Doubling or halving loops usually preserve a bound and a relationship between the changing value and the number of updates.",
                "- Scan loops usually describe what is known about the processed prefix and keep the index inside valid bounds.",
                "- State-change loops may need disjunctions to describe the initial state separately from the state after the first iteration.",
                "Prefer invariants that mention the variables needed by the postcondition.",
                "Prefer symbolic variables from the precondition or postcondition over fixed trace numbers.",
                "If the loop overwrites a variable on the first iteration, consider an invariant shaped like `(initial_case) or (after_iteration_case)`.",
                "Do not merely restate the postcondition unless it is already true throughout the loop.",
                "Before returning a candidate, mentally check establishment, preservation, and postcondition implication.",
                "Return only candidate invariants between CANDIDATES and END.",
                "Return at most three candidate invariants.",
                "Put one invariant per line.",
                "Use Python-style boolean syntax: and, or, not.",
                "Do not return JSON, labels, bullet points, code blocks, or explanations.",
                "Output format example:",
                "CANDIDATES",
                "counter >= 0",
                "END",
            ]

            if guidance_examples:
                sections.append("Guidance examples. Use these as patterns, not as answers for the target loop:")
                for example in guidance_examples[:6]:
                    sections.append(f"Example: {example['name']}")
                    sections.append(f"Pattern: {example['pattern']}")
                    sections.append(f"Shape to imitate: {example['invariant']}")
                    if example.get("reasoning_hint"):
                        sections.append(f"Why this shape works: {example['reasoning_hint']}")

        sections.extend(additional_user_sections(prompt_style, frontend_prompt_addition))
        sections.extend(refresh_sections(prompt_refresh_id))

        sections.extend(
            [
                "Now generate invariant hypotheses for the target loop.",
                "PRECONDITION:",
                precondition,
                "POSTCONDITION:",
                postcondition,
            ]
        )

        if mode in {"syntax-only", "syntax-plus-trace"}:
            sections.extend(self._syntax_section(syntax_features))

        if mode in {"trace-only", "syntax-plus-trace"}:
            sections.extend(trace_sections(trace))
            if include_trace_observations:
                sections.extend(self._trace_observations(trace))

        return "\n".join(sections)

    def _syntax_section(self, syntax_features: dict[str, object]) -> list[str]:
        lines = ["PROGRAM:"]
        for statement in syntax_features.get("initialisation", []) or []:
            lines.append(str(statement))

        guard = str(syntax_features.get("guard", "")).strip()
        lines.append(f"while {guard}:")
        for statement in syntax_features.get("body", []) or []:
            lines.extend(f"    {line}" for line in str(statement).splitlines())

        variables = syntax_features.get("variables", []) or []
        lines.extend(
            [
                "VARIABLES:",
                ", ".join(str(variable) for variable in variables),
            ]
        )
        return lines

    def build_relation_prompt(self, mode: str, syntax_features: dict[str, object],
                              trace: ExecutionTrace, precondition: str) -> str:
        sections = ["PRECONDITION:", precondition]
        if mode in {"syntax-only", "syntax-plus-trace"}:
            sections.extend(self._syntax_section(syntax_features))
        if mode in {"trace-only", "syntax-plus-trace"}:
            sections.append("TRACE STATES (labels are not program variables):")
            for row in sample_trace_rows(trace):
                if row.get("iteration") == "...":
                    sections.append("... middle states omitted ...")
                else:
                    sections.append(f"State {row.get('iteration')}: {self._format_state(row)}")
        sections.extend([
            "State one arithmetic expression involving changing program variables whose value stays constant across the supplied loop-head states or updates.",
            "Say what it equals, using named parameters rather than sample constants when possible. Also give any variable bounds supported throughout the evidence.",
            "Return only up to three short equations or bounds. Do not return assignments, the guard, final-state facts, or narrative about control flow.",
            "State labels are not program variables. These are tentative relationships, not proofs. Do not invent missing source code.",
        ])
        return "\n".join(sections)

    def _trace_observations(self, trace: ExecutionTrace) -> list[str]:
        if not trace.rows:
            return ["Trace observations: no trace rows were produced."]

        variables = [
            variable
            for variable in trace.rows[0]
            if variable != "iteration" and self._is_numeric_column(trace.rows, variable)
        ]
        sections = [
            "Trace observations:",
            f"trace row count: {len(trace.rows)}",
            f"observed variables: {', '.join(variables)}",
            f"first state: {self._format_state(trace.rows[0])}",
            f"last state: {self._format_state(trace.rows[-1])}",
            f"last observed iteration: {trace.rows[-1].get('iteration')}",
        ]

        for variable in variables:
            values = [row[variable] for row in trace.rows if variable in row]
            sections.append(f"{variable} values: {self._format_values(values[:8])}")
            deltas = [right - left for left, right in zip(values, values[1:])]
            if deltas and all(delta == deltas[0] for delta in deltas):
                sections.append(f"{variable} changes by {deltas[0]} each step.")
            elif deltas:
                sections.append(f"{variable} per-iteration changes: {self._format_values(deltas[:7])}")
            if values and all(value == values[0] for value in values):
                sections.append(f"{variable} stays constant at {values[0]}.")
            one_time_change = self._one_time_change(values)
            if one_time_change:
                sections.append(f"{variable} {one_time_change}")

        relation_sections = self._pair_relation_observations(trace.rows, variables)
        if relation_sections:
            sections.append("Observed pair relationships:")
            sections.extend(relation_sections)

        sections.extend(
            [
                "Trace-only invariant guidance:",
                "- If a variable changes once and then stays fixed, consider a disjunctive invariant with an initial-state case and an after-update case.",
                "- Use the postcondition bound when it identifies the loop limit.",
                "- If one variable decreases while another increases by the same amount, consider a conservation invariant such as `x + y == n`.",
                "- Avoid returning only a sampled fact unless it is preserved and helps imply the postcondition.",
                "Infer a general invariant; prefer symbolic variables from the precondition/postcondition over hard-coded sample rows.",
            ]
        )
        return sections

    def _pair_relation_observations(
        self,
        rows: list[dict[str, Any]],
        variables: list[str],
    ) -> list[str]:
        observations: list[str] = []
        for left, right in combinations(variables, 2):
            pairs = [(row[left], row[right]) for row in rows if left in row and right in row]
            if not pairs:
                continue

            sums = [left_value + right_value for left_value, right_value in pairs]
            if sums and all(value == sums[0] for value in sums):
                observations.append(f"{left} + {right} stays constant at {sums[0]}.")

            differences = [left_value - right_value for left_value, right_value in pairs]
            if differences and all(value == differences[0] for value in differences):
                observations.append(f"{left} - {right} stays constant at {differences[0]}.")

            if all(left_value <= right_value for left_value, right_value in pairs):
                observations.append(f"{left} <= {right} holds in every observed state.")
            if all(left_value >= right_value for left_value, right_value in pairs):
                observations.append(f"{left} >= {right} holds in every observed state.")

        return observations[:12]

    def _state_without_iteration(self, row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key != "iteration"}

    def _format_state(self, row: dict[str, Any]) -> str:
        return ", ".join(
            f"{key}={value}"
            for key, value in row.items()
            if key != "iteration"
        )

    def _format_values(self, values: list[Any]) -> str:
        return ", ".join(str(value) for value in values)

    def _is_numeric_column(self, rows: list[dict[str, Any]], variable: str) -> bool:
        return all(isinstance(row.get(variable), int) for row in rows if variable in row)

    def _one_time_change(self, values: list[Any]) -> str:
        if len(values) < 3:
            return ""

        first_value = values[0]
        second_value = values[1]
        if first_value != second_value and all(value == second_value for value in values[1:]):
            return f"changes from {first_value} to {second_value} after the first iteration and then stays {second_value}."

        for index in range(1, len(values) - 1):
            before = values[:index]
            after = values[index:]
            if before and after and all(value == before[0] for value in before) and all(value == after[0] for value in after) and before[0] != after[0]:
                return f"has a single phase change from {before[0]} to {after[0]} around observed iteration {index}."

        return ""
