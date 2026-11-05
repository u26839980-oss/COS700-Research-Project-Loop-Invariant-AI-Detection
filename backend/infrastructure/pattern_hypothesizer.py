from __future__ import annotations

import re
from itertools import combinations
from typing import Any

from backend.domain.models import ExecutionTrace
from backend.infrastructure.loop_parser import ParsedLoopProgram


class PatternHypothesizer:
    def generate(self, parsed: ParsedLoopProgram, trace: ExecutionTrace) -> list[str]:
        if not trace.rows:
            return []

        variables = [name for name in parsed.variables if self._is_numeric_column(trace.rows, name)]
        candidates: list[str] = []

        candidates.extend(self._constant_variables(trace.rows, variables))
        pair_sums = self._constant_pair_sums(trace.rows, variables)
        candidates.extend(pair_sums)
        candidates.extend(self._constant_pair_differences(trace.rows, variables))
        bounds = self._simple_bounds(trace.rows, variables)
        candidates.extend(bounds)
        candidates.extend(self._combined_candidates(pair_sums, bounds))

        return self._dedupe(candidates)

    def explain(self, candidate: str) -> str:
        if " and " in candidate:
            parts = candidate.split(" and ")
            return "Combined trace patterns: " + "; ".join(self.explain(part) for part in parts)
        if "+" in candidate and "==" in candidate:
            return "The trace showed this sum staying constant across all sampled loop iterations."
        if "-" in candidate and "==" in candidate:
            return "The trace showed this difference staying constant across all sampled loop iterations."
        if ">=" in candidate or "<=" in candidate:
            return "The trace showed this bound holding across all sampled loop iterations."
        if "==" in candidate:
            return "The trace showed this value staying unchanged across all sampled loop iterations."
        return "The candidate was derived from a repeated pattern in the execution trace."

    def _constant_variables(self, rows: list[dict[str, Any]], variables: list[str]) -> list[str]:
        candidates: list[str] = []
        for variable in variables:
            values = [row[variable] for row in rows if variable in row]
            if values and all(value == values[0] for value in values):
                candidates.append(f"{variable} == {values[0]}")
        return candidates

    def _combined_candidates(self, relations: list[str], bounds: list[str]) -> list[str]:
        candidates: list[str] = []
        for relation in relations:
            relation_variables = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", relation))
            for bound in bounds:
                bound_variables = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", bound))
                if relation_variables.intersection(bound_variables):
                    candidates.append(f"{relation} and {bound}")
        return candidates

    def _constant_pair_sums(self, rows: list[dict[str, Any]], variables: list[str]) -> list[str]:
        candidates: list[str] = []
        for left, right in combinations(variables, 2):
            sums = [row[left] + row[right] for row in rows if left in row and right in row]
            if not sums or not all(value == sums[0] for value in sums):
                continue
            constant_name = self._matching_constant_variable(rows, variables, sums[0], {left, right})
            if constant_name:
                candidates.append(f"{left} + {right} == {constant_name}")
            else:
                candidates.append(f"{left} + {right} == {sums[0]}")
        return candidates

    def _constant_pair_differences(self, rows: list[dict[str, Any]], variables: list[str]) -> list[str]:
        candidates: list[str] = []
        for left, right in combinations(variables, 2):
            differences = [row[left] - row[right] for row in rows if left in row and right in row]
            if differences and all(value == differences[0] for value in differences):
                candidates.append(f"{left} - {right} == {differences[0]}")
        return candidates

    def _simple_bounds(self, rows: list[dict[str, Any]], variables: list[str]) -> list[str]:
        candidates: list[str] = []
        for variable in variables:
            values = [row[variable] for row in rows if variable in row]
            if values and min(values) >= 0:
                candidates.append(f"{variable} >= 0")

        for left, right in combinations(variables, 2):
            left_values = [row[left] for row in rows if left in row and right in row]
            right_values = [row[right] for row in rows if left in row and right in row]
            if left_values and all(a <= b for a, b in zip(left_values, right_values)):
                candidates.append(f"{left} <= {right}")
            if left_values and all(a >= b for a, b in zip(left_values, right_values)):
                candidates.append(f"{left} >= {right}")
        return candidates

    def _matching_constant_variable(
        self,
        rows: list[dict[str, Any]],
        variables: list[str],
        value: int,
        exclude: set[str],
    ) -> str:
        for variable in variables:
            if variable in exclude:
                continue
            values = [row[variable] for row in rows if variable in row]
            if values and all(item == value for item in values):
                return variable
        return ""

    def _is_numeric_column(self, rows: list[dict[str, Any]], variable: str) -> bool:
        return all(isinstance(row.get(variable), int) for row in rows if variable in row)

    def _dedupe(self, candidates: list[str]) -> list[str]:
        deduped: list[str] = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped[:10]
