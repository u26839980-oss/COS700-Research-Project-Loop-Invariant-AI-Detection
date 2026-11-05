from __future__ import annotations

import ast
from dataclasses import dataclass

from backend.domain.models import VerificationResult
from backend.infrastructure.loop_parser import (
    ParsedLoopProgram,
    expression_names,
    parse_expression,
    LoopParseError,
)


class Z3AvailabilityError(RuntimeError):
    pass


class Z3FormulaError(ValueError):
    pass


@dataclass(frozen=True)
class ObligationCheck:
    name: str
    status: str
    counterexample: dict[str, str] | None = None
    diagnostic: str = ""


class Z3InvariantChecker:
    def __init__(self, timeout_ms: int = 3000) -> None:
        try:
            import z3  # type: ignore
        except ImportError as exc:
            raise Z3AvailabilityError(
                "z3-solver is not installed. Install it before running formal checks."
            ) from exc
        self.z3 = z3
        self.timeout_ms = timeout_ms

    def verify(
        self,
        parsed: ParsedLoopProgram,
        precondition: str,
        invariant: str,
        postcondition: str,
    ) -> VerificationResult:
        if not invariant.strip():
            return VerificationResult(
                candidate=invariant,
                accepted=False,
                establishment="not checked",
                preservation="not checked",
                postcondition="not checked",
                error="No invariant was provided.",
                hoare_steps=["No candidate invariant was provided, so no Hoare obligations were built."],
            )

        try:
            variables = sorted(
                set(parsed.variables)
                | expression_names([precondition, invariant])
            )
            symbols = {name: self.z3.Int(name) for name in variables}
            after_prefix = self._execute_symbolic(parsed.prefix, symbols)
            after_body = self._execute_symbolic(parsed.loop.body, symbols)
            prefix_steps = self._symbolic_step_descriptions(parsed.prefix, symbols)
            body_steps = self._symbolic_step_descriptions(parsed.loop.body, symbols)

            pre_before_prefix = self._expr(precondition, symbols)
            invariant_after_prefix = self._expr(invariant, after_prefix)
            invariant_before = self._expr(invariant, symbols)
            guard_before = self._expr_ast(parsed.loop.test, symbols)
            invariant_after_body = self._expr(invariant, after_body)

            feasibility = self.z3.Solver()
            feasibility.set(timeout=self.timeout_ms)
            feasibility.add(pre_before_prefix)
            feasible = feasibility.check()
            if feasible != self.z3.sat:
                return VerificationResult(
                    candidate=invariant, accepted=False,
                    establishment="infeasible" if feasible == self.z3.unsat else "unknown",
                    preservation="not checked", postcondition="not checked",
                    error="Precondition is unsatisfiable or feasibility could not be decided.",
                )
            establishment = self._prove_implies(pre_before_prefix, invariant_after_prefix,
                                                {"initial": symbols, "loop_head": after_prefix})
            preservation = self._prove_implies(
                self.z3.And(invariant_before, guard_before),
                invariant_after_body,
                {"before": symbols, "after": after_body},
            )
            try:
                post_before = self._expr(postcondition, symbols)
                postcondition_result = self._prove_implies(
                    self.z3.And(invariant_before, self.z3.Not(guard_before)),
                    post_before, {"exit": symbols},
                )
            except (LoopParseError, Z3FormulaError, self.z3.Z3Exception, TypeError) as exc:
                postcondition_result = ObligationCheck("postcondition", "unsupported", diagnostic=str(exc))

            accepted = all(
                item.status == "proved"
                for item in [establishment, preservation, postcondition_result]
            )
            return VerificationResult(
                candidate=invariant,
                accepted=accepted,
                establishment=establishment.status,
                preservation=preservation.status,
                postcondition=postcondition_result.status,
                error="; ".join(
                    f"{name}: {check.diagnostic}" for name, check in (
                        ("establishment", establishment), ("preservation", preservation),
                        ("postcondition", postcondition_result),
                    ) if check.diagnostic
                ),
                hoare_steps=self._hoare_steps(
                    parsed=parsed,
                    precondition=precondition,
                    invariant=invariant,
                    postcondition=postcondition,
                    prefix_steps=prefix_steps,
                    body_steps=body_steps,
                    establishment=establishment.status,
                    preservation=preservation.status,
                    postcondition_result=postcondition_result.status,
                ),
                counterexamples={
                    name: check.counterexample for name, check in (
                        ("establishment", establishment), ("preservation", preservation),
                        ("postcondition", postcondition_result),
                    ) if check.counterexample is not None
                },
            )
        except Z3FormulaError as exc:
            return VerificationResult(
                candidate=invariant, accepted=False, establishment="unsupported",
                preservation="unsupported", postcondition="unsupported", error=str(exc),
            )
        except Exception as exc:
            return VerificationResult(
                candidate=invariant,
                accepted=False,
                establishment="error",
                preservation="error",
                postcondition="error",
                error=str(exc),
                hoare_steps=[f"Could not build Hoare obligations because: {exc}"],
            )

    def _prove_implies(self, premise: object, conclusion: object,
                       states: dict[str, dict[str, object]] | None = None) -> ObligationCheck:
        solver = self.z3.Solver()
        solver.set(timeout=self.timeout_ms)
        solver.add(self.z3.And(premise, self.z3.Not(conclusion)))
        result = solver.check()
        if result == self.z3.unsat:
            return ObligationCheck(name="", status="proved")
        if result == self.z3.sat:
            model = solver.model()
            witness = {
                f"{stage}.{name}": str(model.eval(value, model_completion=True))
                for stage, state in (states or {}).items() for name, value in state.items()
            } if states else {str(declaration): str(model[declaration]) for declaration in model.decls()}
            return ObligationCheck(name="", status="failed", counterexample=witness)
        return ObligationCheck(name="", status="unknown", diagnostic=solver.reason_unknown())

    def _execute_symbolic(
        self,
        statements: list[ast.stmt],
        initial_symbols: dict[str, object],
    ) -> dict[str, object]:
        symbols = dict(initial_symbols)
        for stmt in statements:
            match stmt:
                case ast.Assign(targets=[ast.Name(id=target)], value=value):
                    symbols[target] = self._expr_ast(value, symbols)
                case ast.AugAssign(target=ast.Name(id=target), op=operator, value=value):
                    symbols[target] = self._binop(operator, symbols[target], self._expr_ast(value, symbols))
                case ast.If():
                    symbols = self._execute_symbolic_if(stmt, symbols)
                case _:
                    raise Z3FormulaError(f"Unsupported statement for Z3: {ast.unparse(stmt)}")
        return symbols

    def _execute_symbolic_if(
        self,
        stmt: ast.If,
        initial_symbols: dict[str, object],
    ) -> dict[str, object]:
        condition = self._expr_ast(stmt.test, initial_symbols)
        then_symbols = self._execute_symbolic(stmt.body, dict(initial_symbols))
        else_symbols = self._execute_symbolic(stmt.orelse, dict(initial_symbols))
        assigned_names = self._assigned_names(stmt.body) | self._assigned_names(stmt.orelse)

        merged = dict(initial_symbols)
        for name in assigned_names:
            then_value = then_symbols.get(name, initial_symbols.get(name, self.z3.Int(name)))
            else_value = else_symbols.get(name, initial_symbols.get(name, self.z3.Int(name)))
            merged[name] = self.z3.If(condition, then_value, else_value)
        return merged

    def _assigned_names(self, statements: list[ast.stmt]) -> set[str]:
        assigned: set[str] = set()
        for stmt in statements:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                assigned.add(stmt.targets[0].id)
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
                assigned.add(stmt.target.id)
            elif isinstance(stmt, ast.If):
                assigned.update(self._assigned_names(stmt.body))
                assigned.update(self._assigned_names(stmt.orelse))
        return assigned

    def _symbolic_step_descriptions(
        self,
        statements: list[ast.stmt],
        initial_symbols: dict[str, object],
    ) -> list[str]:
        symbols = dict(initial_symbols)
        steps: list[str] = []
        for stmt in statements:
            before = {name: str(value) for name, value in symbols.items()}
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
                value = self._expr_ast(stmt.value, symbols)
                symbols[target] = value
                steps.append(
                    f"Line `{ast.unparse(stmt)}` updates `{target}` to `{value}` from state {before}."
                )
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
                target = stmt.target.id
                current = symbols[target]
                value = self._expr_ast(stmt.value, symbols)
                symbols[target] = self._binop(stmt.op, current, value)
                steps.append(
                    f"Line `{ast.unparse(stmt)}` updates `{target}` to `{symbols[target]}` from state {before}."
                )
            elif isinstance(stmt, ast.If):
                symbols = self._execute_symbolic_if(stmt, symbols)
                steps.append(
                    f"Branch `{ast.unparse(stmt.test)}` is represented symbolically with z3.If from state {before}."
                )
        return steps

    def _expr(self, expression: str, symbols: dict[str, object]) -> object:
        return self._expr_ast(parse_expression(expression).body, symbols)

    def _expr_ast(self, node: ast.AST, symbols: dict[str, object]) -> object:
        match node:
            case ast.Constant(value=bool() as value):
                return self.z3.BoolVal(value)
            case ast.Constant(value=int() as value):
                return self.z3.IntVal(value)
            case ast.Constant():
                raise Z3FormulaError("Only integer and boolean constants are supported.")
            case ast.Name(id=name):
                if name not in symbols:
                    symbols[name] = self.z3.Int(name)
                return symbols[name]
            case ast.BinOp(left=left, op=operator, right=right):
                return self._binop(operator, self._expr_ast(left, symbols), self._expr_ast(right, symbols))
            case ast.UnaryOp(op=operator, operand=operand_node):
                operand = self._expr_ast(operand_node, symbols)
                match operator:
                    case ast.USub():
                        return -operand
                    case ast.UAdd():
                        return operand
                    case ast.Not():
                        return self.z3.Not(operand)
                    case _:
                        raise Z3FormulaError(f"Unsupported unary operator: {type(operator).__name__}")
            case ast.BoolOp(op=operator, values=operands):
                values = [self._expr_ast(value, symbols) for value in operands]
                match operator:
                    case ast.And():
                        return self.z3.And(*values)
                    case ast.Or():
                        return self.z3.Or(*values)
                    case _:
                        raise Z3FormulaError(f"Unsupported boolean operator: {type(operator).__name__}")
            case ast.Compare():
                clauses = []
                left = self._expr_ast(node.left, symbols)
                for operator, comparator in zip(node.ops, node.comparators):
                    right = self._expr_ast(comparator, symbols)
                    clauses.append(self._compare(operator, left, right))
                    left = right
                return self.z3.And(*clauses)
            case _:
                raise Z3FormulaError(f"Unsupported expression for Z3: {type(node).__name__}")

    def _binop(self, operator: ast.operator, left: object, right: object) -> object:
        match operator:
            case ast.Add():
                return left + right
            case ast.Sub():
                return left - right
            case ast.Mult():
                return left * right
            case ast.Div():
                raise Z3FormulaError("Python / uses floating-point arithmetic; use integer // with a positive constant divisor.")
            case ast.FloorDiv():
                return left / self._positive_constant_divisor(right)
            case ast.Mod():
                return left % self._positive_constant_divisor(right)
            case ast.Pow():
                exponent = self.z3.simplify(right)
                if not self.z3.is_int_value(exponent) or not 0 <= exponent.as_long() <= 16:
                    raise Z3FormulaError("Only constant integer powers from 0 through 16 are supported.")
                product = self.z3.IntVal(1)
                for _ in range(exponent.as_long()):
                    product = product * left
                return product
            case _:
                raise Z3FormulaError(f"Unsupported arithmetic operator: {type(operator).__name__}")

    def _positive_constant_divisor(self, value: object) -> object:
        divisor = self.z3.simplify(value)
        if not self.z3.is_int_value(divisor) or divisor.as_long() <= 0:
            raise Z3FormulaError("Only positive constant divisors are supported for // and %.")
        return divisor

    def _compare(self, operator: ast.cmpop, left: object, right: object) -> object:
        match operator:
            case ast.Eq():
                return left == right
            case ast.NotEq():
                return left != right
            case ast.Lt():
                return left < right
            case ast.LtE():
                return left <= right
            case ast.Gt():
                return left > right
            case ast.GtE():
                return left >= right
            case _:
                raise Z3FormulaError(f"Unsupported comparison: {type(operator).__name__}")

    def _hoare_steps(
        self,
        parsed: ParsedLoopProgram,
        precondition: str,
        invariant: str,
        postcondition: str,
        prefix_steps: list[str],
        body_steps: list[str],
        establishment: str,
        preservation: str,
        postcondition_result: str,
    ) -> list[str]:
        steps = [
            f"Candidate invariant I: `{invariant}`.",
            f"Precondition P: `{precondition}`.",
            f"Loop guard B: `{ast.unparse(parsed.loop.test)}`.",
            f"Postcondition Q: `{postcondition}`.",
            "Initialisation is symbolically executed before checking establishment.",
        ]
        steps.extend(prefix_steps or ["No initialisation lines were present before the loop."])
        steps.extend(
            [
                f"Establishment obligation: prove `P(initial state) => I(after initialisation)`. Result: {establishment}.",
                "Preservation assumes the invariant and the loop guard before one iteration.",
            ]
        )
        steps.extend(body_steps or ["No loop-body lines were available for symbolic preservation."])
        steps.extend(
            [
                f"Preservation obligation: prove `I and B => I'` after one loop-body execution. Result: {preservation}.",
                f"Postcondition obligation: prove `I and not B => Q`. Result: {postcondition_result}.",
            ]
        )
        return steps
