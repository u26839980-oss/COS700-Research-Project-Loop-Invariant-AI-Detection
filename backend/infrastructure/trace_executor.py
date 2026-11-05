from __future__ import annotations

import ast
from typing import Any

from backend.domain.models import ExecutionTrace
from backend.infrastructure.loop_parser import ParsedLoopProgram


class TraceExecutionError(RuntimeError):
    pass


class SafeExpressionEvaluator(ast.NodeVisitor):
    def __init__(self, environment: dict[str, int]) -> None:
        self.environment = environment

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> int | bool:
        if isinstance(node.value, bool):
            return node.value
        if isinstance(node.value, int):
            return node.value
        raise TraceExecutionError("Only integer and boolean constants are supported.")

    def visit_Name(self, node: ast.Name) -> int:
        if node.id not in self.environment:
            raise TraceExecutionError(f"Variable '{node.id}' has no runtime value.")
        return self.environment[node.id]

    def visit_BinOp(self, node: ast.BinOp) -> int:
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Div):
            raise TraceExecutionError("Floating-point / is outside the integer subset; use //.")
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise TraceExecutionError(f"Unsupported arithmetic operator: {type(node.op).__name__}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> int | bool:
        value = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.Not):
            return not bool(value)
        raise TraceExecutionError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            return all(bool(self.visit(value)) for value in node.values)
        if isinstance(node.op, ast.Or):
            return any(bool(self.visit(value)) for value in node.values)
        raise TraceExecutionError(f"Unsupported boolean operator: {type(node.op).__name__}")

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for operator, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if isinstance(operator, ast.Eq):
                ok = left == right
            elif isinstance(operator, ast.NotEq):
                ok = left != right
            elif isinstance(operator, ast.Lt):
                ok = left < right
            elif isinstance(operator, ast.LtE):
                ok = left <= right
            elif isinstance(operator, ast.Gt):
                ok = left > right
            elif isinstance(operator, ast.GtE):
                ok = left >= right
            else:
                raise TraceExecutionError(f"Unsupported comparison: {type(operator).__name__}")
            if not ok:
                return False
            left = right
        return True


class TraceExecutor:
    def execute(
        self,
        parsed: ParsedLoopProgram,
        initial_environment: dict[str, int],
        iteration_limit: int,
    ) -> ExecutionTrace:
        environment = dict(initial_environment)
        for stmt in parsed.prefix:
            self._execute_statement(stmt, environment)

        rows = [self._trace_row(0, parsed.variables, environment)]
        for iteration in range(1, iteration_limit + 1):
            guard_value = self._evaluate_node(parsed.loop.test, environment)
            if not bool(guard_value):
                break
            for stmt in parsed.loop.body:
                self._execute_statement(stmt, environment)
            rows.append(self._trace_row(iteration, parsed.variables, environment))
        else:
            if bool(self._evaluate_node(parsed.loop.test, environment)):
                raise TraceExecutionError(
                    f"Loop did not terminate within the iteration limit of {iteration_limit}."
                )

        return ExecutionTrace(rows=rows)

    def _execute_statement(self, stmt: ast.stmt, environment: dict[str, int]) -> None:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            environment[stmt.targets[0].id] = int(self._evaluate_node(stmt.value, environment))
            return

        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            current = environment.get(stmt.target.id, 0)
            value = int(self._evaluate_node(stmt.value, environment))
            synthetic = ast.BinOp(left=ast.Constant(current), op=stmt.op, right=ast.Constant(value))
            environment[stmt.target.id] = int(self._evaluate_node(synthetic, environment))
            return

        if isinstance(stmt, ast.If):
            branch = stmt.body if bool(self._evaluate_node(stmt.test, environment)) else stmt.orelse
            for branch_stmt in branch:
                self._execute_statement(branch_stmt, environment)
            return

        raise TraceExecutionError(f"Unsupported statement in trace execution: {ast.unparse(stmt)}")

    def _evaluate_node(self, node: ast.AST, environment: dict[str, int]) -> Any:
        evaluator = SafeExpressionEvaluator(environment)
        return evaluator.visit(node)

    def _trace_row(
        self,
        iteration: int,
        variables: list[str],
        environment: dict[str, int],
    ) -> dict[str, int]:
        row = {"iteration": iteration}
        for variable in variables:
            if variable in environment:
                row[variable] = environment[variable]
        return row
