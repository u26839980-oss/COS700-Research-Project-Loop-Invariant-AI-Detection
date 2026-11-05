from __future__ import annotations

import re


class ReverseConstructionError(ValueError):
    pass


class ReverseLoopConstructor:
    def construct(
        self,
        invariant: str,
        precondition: str,
        postcondition: str,
        guard: str,
    ) -> tuple[str, list[str]]:
        cleaned_invariant = self._normalize(invariant)
        cleaned_guard = self._normalize(guard)

        sum_result = self._construct_transfer_loop(cleaned_invariant, cleaned_guard)
        if sum_result:
            return sum_result

        sum_to_n_result = self._construct_sum_to_n_loop(cleaned_invariant, cleaned_guard)
        if sum_to_n_result:
            return sum_to_n_result

        even_result = self._construct_even_counter_loop(cleaned_invariant, cleaned_guard)
        if even_result:
            return even_result

        raise ReverseConstructionError(
            "No reverse-construction pattern matched this invariant yet. "
            "Try an invariant shaped like `x + y == n and x >= 0` with guard `x > 0`."
        )

    def _construct_transfer_loop(
        self,
        invariant: str,
        guard: str,
    ) -> tuple[str, list[str]] | None:
        sum_match = re.search(
            r"\b([A-Za-z_]\w*)\s*\+\s*([A-Za-z_]\w*)\s*==\s*([A-Za-z_]\w*)\b",
            invariant,
        )
        guard_match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*>\s*0\s*", guard)
        if not sum_match or not guard_match:
            return None

        left, right, total = sum_match.groups()
        decreasing = guard_match.group(1)
        if decreasing not in {left, right}:
            return None
        increasing = right if decreasing == left else left

        program = "\n".join(
            [
                f"{total} = 5",
                f"{decreasing} = {total}",
                f"{increasing} = 0",
                f"while {decreasing} > 0:",
                f"    {decreasing} = {decreasing} - 1",
                f"    {increasing} = {increasing} + 1",
                "",
            ]
        )
        steps = [
            f"Detected conservation relation `{left} + {right} == {total}`.",
            f"Used guard `{guard}` to select `{decreasing}` as the progress variable.",
            f"Initialised `{decreasing}` to `{total}` and `{increasing}` to `0` so the invariant is established.",
            f"Chose body updates `{decreasing} = {decreasing} - 1` and `{increasing} = {increasing} + 1` so the sum is preserved.",
            f"The loop exits when `{decreasing} <= 0`; with the bound in the invariant, this supports the postcondition.",
        ]
        return program, steps

    def _construct_sum_to_n_loop(
        self,
        invariant: str,
        guard: str,
    ) -> tuple[str, list[str]] | None:
        has_sum_formula = re.search(
            r"\b([A-Za-z_]\w*)\s*==\s*([A-Za-z_]\w*)\s*\*\s*\(\s*\2\s*\+\s*1\s*\)\s*/\s*2",
            invariant,
        )
        guard_match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*<\s*([A-Za-z_]\w*)\s*", guard)
        if not has_sum_formula or not guard_match:
            return None

        total_var, index_var = has_sum_formula.groups()
        guard_index, limit = guard_match.groups()
        if guard_index != index_var:
            return None

        program = "\n".join(
            [
                f"{limit} = 5",
                f"{index_var} = 0",
                f"{total_var} = 0",
                f"while {index_var} < {limit}:",
                f"    {index_var} = {index_var} + 1",
                f"    {total_var} = {total_var} + {index_var}",
                "",
            ]
        )
        steps = [
            f"Detected triangular-number relation for `{total_var}` and `{index_var}`.",
            f"Used guard `{guard}` to grow `{index_var}` toward `{limit}`.",
            f"Initialised `{index_var}` and `{total_var}` to `0`, establishing the closed-form sum at loop entry.",
            f"Incremented `{index_var}` before adding it to `{total_var}` so the next triangular number is preserved.",
        ]
        return program, steps

    def _construct_even_counter_loop(
        self,
        invariant: str,
        guard: str,
    ) -> tuple[str, list[str]] | None:
        relation_match = re.search(
            r"\b([A-Za-z_]\w*)\s*==\s*2\s*\*\s*([A-Za-z_]\w*)\b",
            invariant,
        )
        guard_match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*<\s*([A-Za-z_]\w*)\s*", guard)
        if not relation_match or not guard_match:
            return None

        doubled, counter = relation_match.groups()
        guard_var, limit = guard_match.groups()
        if guard_var != doubled:
            return None

        program = "\n".join(
            [
                f"{limit} = 6",
                f"{doubled} = 0",
                f"{counter} = 0",
                f"while {doubled} < {limit}:",
                f"    {doubled} = {doubled} + 2",
                f"    {counter} = {counter} + 1",
                "",
            ]
        )
        steps = [
            f"Detected scaling relation `{doubled} == 2 * {counter}`.",
            f"Used guard `{guard}` to advance `{doubled}` toward `{limit}`.",
            f"Initialised both related variables to `0`, establishing the invariant at loop entry.",
            f"Chose paired updates `+2` and `+1` to preserve the scaling relation.",
        ]
        return program, steps

    def _normalize(self, text: str) -> str:
        return text.strip().replace("&&", " and ").replace("||", " or ")
