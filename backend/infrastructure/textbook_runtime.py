"""Restricted interpreter for textbook examples; never executes user Python code."""
from __future__ import annotations

import ast
from copy import deepcopy

from backend.domain.models import ExecutionTrace
from backend.infrastructure.trace_executor import SafeExpressionEvaluator, TraceExecutionError


class TextbookEvaluator(SafeExpressionEvaluator):
    def __init__(self, environment, budget=None):
        super().__init__(environment)
        self.budget = budget if budget is not None else [100000]

    def visit(self, node):
        self.budget[0] -= 1
        if self.budget[0] < 0:
            raise TraceExecutionError("Expression evaluation budget exceeded.")
        return super().visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Pow):
            base, exponent = self.visit(node.left), self.visit(node.right)
            if not isinstance(exponent, int) or not 0 <= exponent <= 10000:
                raise TraceExecutionError("Sample powers require integer exponents from 0 to 10000.")
            if abs(base).bit_length() * exponent > 20000:
                raise TraceExecutionError("Sample power exceeds the numeric size budget.")
            return base ** exponent
        value = super().visit_BinOp(node)
        if isinstance(value, int) and value.bit_length() > 20000:
            raise TraceExecutionError("Sample arithmetic exceeds the numeric size budget.")
        return value

    def generic_visit(self, node):
        raise TraceExecutionError(f"Unsupported textbook expression: {type(node).__name__}")

    def visit_IfExp(self, node):
        return self.visit(node.body if self.visit(node.test) else node.orelse)

    def visit_Subscript(self, node):
        value = self.visit(node.value)
        if isinstance(node.slice, ast.Slice):
            if node.slice.step:
                raise TraceExecutionError("Slice steps are unsupported.")
            start = self.visit(node.slice.lower) if node.slice.lower else None
            stop = self.visit(node.slice.upper) if node.slice.upper else None
            return value[start:stop]
        index = self.visit(node.slice)
        # Mathematical array indices never wrap around as Python negative indices do.
        if not 0 <= index < len(value):
            raise TraceExecutionError(f"Array index {index} outside [0, {len(value)}).")
        return value[index]

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise TraceExecutionError("Only approved positional function calls are supported.")
        name = node.func.id
        if name in {"all", "any"} and len(node.args) == 1 and isinstance(node.args[0], ast.GeneratorExp):
            gen = node.args[0]
            if len(gen.generators) != 1 or gen.generators[0].ifs or gen.generators[0].is_async:
                raise TraceExecutionError("Only one unfiltered bounded generator is supported.")
            clause = gen.generators[0]
            if not isinstance(clause.target, ast.Name):
                raise TraceExecutionError("Generator target must be a name.")
            def values():
                for value in self.visit(clause.iter):
                    nested = dict(self.environment, **{clause.target.id: value})
                    yield bool(TextbookEvaluator(nested, self.budget).visit(gen.elt))
            return all(values()) if name == "all" else any(values())
        args = [self.visit(arg) for arg in node.args]
        if name in {"len", "max"} and len(args) == 1:
            return len(args[0]) if name == "len" else max(args[0])
        if name == "range" and 1 <= len(args) <= 2:
            values = range(*args)
            if len(values) > 10000:
                raise TraceExecutionError("Quantifier sample exceeds 10000 elements.")
            return values
        raise TraceExecutionError(f"Unsupported function: {name}")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, right_node in zip(node.ops, node.comparators):
            right = self.visit(right_node)
            if isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            else:
                operators = {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right,
                             ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right,
                             ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right}
                if type(op) not in operators:
                    raise TraceExecutionError("Unsupported comparison.")
                ok = operators[type(op)]()
            if not ok:
                return False
            left = right
        return True


def evaluate(expression, environment):
    return TextbookEvaluator(environment).visit(ast.parse(expression, mode="eval"))


def split_program(program):
    tree = ast.parse(program)
    loops = [index for index, stmt in enumerate(tree.body) if isinstance(stmt, ast.While)]
    if len(loops) != 1:
        raise ValueError("Expected exactly one top-level while loop.")
    index = loops[0]
    return tree.body[:index], tree.body[index], tree.body[index + 1:]


def execute(program, inputs, precondition, iteration_limit=100, statement_limit=100000):
    if iteration_limit < 1:
        raise ValueError("Iteration limit must be positive.")
    if any(not isinstance(value, int) or isinstance(value, bool) for item in inputs.values()
           for value in (item if isinstance(item, list) else [item])):
        raise ValueError("Sample inputs must be integers or lists of integers.")
    if any(isinstance(item, list) and len(item) > 10000 for item in inputs.values()):
        raise ValueError("Sample arrays may contain at most 10000 elements.")
    environment = deepcopy(inputs)
    if not evaluate(precondition, environment):
        raise ValueError("Sample input does not satisfy the precondition.")
    prefix, loop, suffix = split_program(program)
    remaining = statement_limit

    def statements(body):
        nonlocal remaining
        for stmt in body:
            remaining -= 1
            if remaining < 0:
                raise TraceExecutionError("Statement budget exceeded.")
            evaluator = TextbookEvaluator(environment)
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                value = evaluator.visit(stmt.value)
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    environment[target.id] = deepcopy(value)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    index = evaluator.visit(target.slice)
                    array = environment[target.value.id]
                    if not 0 <= index < len(array):
                        raise TraceExecutionError("Array write outside its bounds.")
                    array[index] = value
                else:
                    raise TraceExecutionError("Unsupported assignment target.")
            elif isinstance(stmt, ast.If):
                statements(stmt.body if evaluator.visit(stmt.test) else stmt.orelse)
            elif isinstance(stmt, ast.While) and not stmt.orelse:
                while TextbookEvaluator(environment).visit(stmt.test):
                    remaining -= 1
                    if remaining < 0:
                        raise TraceExecutionError("Nested-loop statement budget exceeded.")
                    statements(stmt.body)
            else:
                raise TraceExecutionError(f"Unsupported statement: {type(stmt).__name__}")

    statements(prefix)
    rows = [{"iteration": 0, **deepcopy(environment)}]
    iterations = 0
    while TextbookEvaluator(environment).visit(loop.test):
        if iterations >= iteration_limit:
            raise TraceExecutionError(f"Textbook loop exceeds iteration limit {iteration_limit}.")
        statements(loop.body)
        iterations += 1
        rows.append({"iteration": iterations, **deepcopy(environment)})
    statements(suffix)
    return ExecutionTrace(rows), deepcopy(environment)
