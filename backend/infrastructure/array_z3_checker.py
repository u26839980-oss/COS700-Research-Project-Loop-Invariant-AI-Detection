"""Hoare obligations over integer arrays, bounded scans and natural powers.

Array lengths and contents are symbolic, not bounded to the sample trace.
Array accesses require nonnegative in-range indices; slices use Python clipping.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, replace

from backend.domain.models import VerificationResult
from backend.domain.textbook_contract import TextbookProofContext, free_names, preserved_assumptions
from backend.infrastructure.textbook_runtime import split_program
from backend.infrastructure.z3_checker import Z3InvariantChecker, Z3FormulaError, ObligationCheck
from backend.infrastructure.integer_power_theory import IntegerPowerTheory
from backend.infrastructure.scan_loop_inference import infer_scan_annotation


@dataclass(frozen=True)
class ArrayView:
    data: object
    length: object
    start: object = 0


@dataclass(frozen=True)
class Maximum:
    array: ArrayView


class ArrayZ3Checker(Z3InvariantChecker):
    def __init__(self, timeout_ms=3000):
        super().__init__(timeout_ms)
        self.powers = IntegerPowerTheory(self.z3)
        if not self.powers.square_induction_certificate():
            raise Z3FormulaError("Natural-power square-law induction certificate did not pass.")
        self.scan_annotations = []

    def _prove_implies(self, premise, conclusion, states=None):
        z = self.z3
        terms = self.powers.terms(premise, conclusion)
        result = super()._prove_implies(z.And(premise, *self.powers.facts(terms)), conclusion, states)
        def contains_power(node):
            return (z.is_app(node) and node.decl() == self.powers.power) or any(
                contains_power(child) for child in node.children())
        if result.status == "failed" and (contains_power(premise) or contains_power(conclusion)):
            # An arbitrary UF interpretation need not be real exponentiation.
            witness = self.powers.concrete_counterexample(premise, conclusion, self.timeout_ms)
            if witness is not None:
                return ObligationCheck("", "failed", counterexample=witness)
            return ObligationCheck("", "unknown", diagnostic="Power abstraction is inconclusive; its SAT model is not a certified integer-power counterexample.")
        return result

    def _expression(self, text, state):
        return self._term(ast.parse(text, mode="eval").body, state)

    def _term(self, node, state):
        z = self.z3
        if isinstance(node, ast.Constant) and type(node.value) in (bool, int):
            return (z.BoolVal(node.value) if type(node.value) is bool else z.IntVal(node.value)), z.BoolVal(True)
        if isinstance(node, ast.Name):
            if node.id not in state:
                raise Z3FormulaError(f"Unknown variable: {node.id}")
            return state[node.id], z.BoolVal(True)
        if isinstance(node, ast.IfExp):
            if isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
                return self._term(node.body if node.test.value else node.orelse, state)
            test, td = self._term(node.test, state)
            yes, yd = self._term(node.body, state)
            no, nd = self._term(node.orelse, state)
            if not z.is_bool(test) or isinstance(yes, (ArrayView, Maximum)) or isinstance(no, (ArrayView, Maximum)):
                raise Z3FormulaError("Conditional expressions require a Boolean test and scalar branches.")
            return z.If(test, yes, no), z.And(td, z.If(test, yd, nd))
        if isinstance(node, ast.BinOp):
            left, ld = self._term(node.left, state)
            right, rd = self._term(node.right, state)
            if isinstance(node.op, ast.Pow):
                exponent = z.simplify(right)
                if z.is_int_value(exponent) and 0 <= exponent.as_long() <= 16:
                    return self._binop(node.op, left, right), z.And(ld, rd)
                return self.powers.power(left, right), z.And(ld, rd, right >= 0)
            return self._binop(node.op, left, right), z.And(ld, rd)
        if isinstance(node, ast.UnaryOp):
            value, defined = self._term(node.operand, state)
            if isinstance(node.op, ast.Not):
                return z.Not(value), defined
            if isinstance(node.op, ast.USub):
                return -value, defined
            if isinstance(node.op, ast.UAdd):
                return value, defined
        if isinstance(node, ast.BoolOp):
            values, checks = [], []
            path = z.BoolVal(True)
            for child in node.values:
                value, defined = self._term(child, state)
                checks.append(z.Implies(path, defined))
                values.append(value)
                path = z.And(path, value if isinstance(node.op, ast.And) else z.Not(value))
            return (z.And(*values) if isinstance(node.op, ast.And) else z.Or(*values)), z.And(*checks)
        if isinstance(node, ast.Subscript):
            array, defined = self._term(node.value, state)
            if not isinstance(array, ArrayView):
                raise Z3FormulaError("Subscript requires an array.")
            if isinstance(node.slice, ast.Slice):
                if node.slice.step:
                    raise Z3FormulaError("Slice steps unsupported.")
                def endpoint(part, default):
                    if part is None:
                        return default, z.BoolVal(True)
                    value, safe = self._term(part, state)
                    value = z.If(value < 0, array.length + value, value)
                    return z.If(value < 0, 0, z.If(value > array.length, array.length, value)), safe
                start, sd = endpoint(node.slice.lower, z.IntVal(0))
                stop, ed = endpoint(node.slice.upper, array.length)
                return ArrayView(array.data, z.If(stop >= start, stop - start, 0), array.start + start), z.And(defined, sd, ed)
            index, safe = self._term(node.slice, state)
            return z.Select(array.data, array.start + index), z.And(defined, safe, index >= 0, index < array.length)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name in {"len", "max"} and len(node.args) == 1:
                array, defined = self._term(node.args[0], state)
                if not isinstance(array, ArrayView):
                    raise Z3FormulaError(f"{name} requires an array.")
                return (array.length, defined) if name == "len" else (Maximum(array), z.And(defined, array.length > 0))
            if name in {"all", "any"} and len(node.args) == 1 and isinstance(node.args[0], ast.GeneratorExp):
                gen = node.args[0]
                if len(gen.generators) != 1:
                    raise Z3FormulaError("Only a single bounded generator is supported.")
                clause = gen.generators[0]
                if clause.ifs or clause.is_async or not isinstance(clause.target, ast.Name):
                    raise Z3FormulaError("Generator must be synchronous and unfiltered.")
                call = clause.iter
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == "range" and not call.keywords and 1 <= len(call.args) <= 2):
                    raise Z3FormulaError("Quantifiers require range(stop) or range(start, stop).")
                bounds = [self._term(arg, state) for arg in call.args]
                start = bounds[0][0] if len(bounds) == 2 else z.IntVal(0)
                stop = bounds[-1][0]
                index = z.FreshInt("index")
                value, defined = self._term(gen.elt, dict(state, **{clause.target.id: index}))
                in_range = z.And(index >= start, index < stop)
                predicate = z.ForAll([index], z.Implies(in_range, value)) if name == "all" else z.Exists([index], z.And(in_range, value))
                return predicate, z.And(*(b[1] for b in bounds), z.ForAll([index], z.Implies(in_range, defined)))
        if isinstance(node, ast.Compare):
            left, ld = self._term(node.left, state)
            values, checks = [], [ld]
            path = z.BoolVal(True)
            for operator, child in zip(node.ops, node.comparators):
                right, rd = self._term(child, state)
                value = self._relation(operator, left, right)
                values.append(value)
                checks.append(z.Implies(path, rd))
                path = z.And(path, value)
                left = right
            return z.And(*values), z.And(*checks)
        raise Z3FormulaError(f"Unsupported array expression: {ast.dump(node, include_attributes=False)}")

    def _relation(self, op, left, right):
        z = self.z3
        if isinstance(op, (ast.In, ast.NotIn)):
            if not isinstance(right, ArrayView):
                raise Z3FormulaError("Membership requires an array.")
            index = z.FreshInt("member")
            inside = z.And(index >= right.start, index < right.start + right.length)
            equal = z.Select(right.data, index) == left
            # Absolute indices avoid shifted quantified array terms for slices.
            return (z.ForAll([index], z.Implies(inside, z.Not(equal))) if isinstance(op, ast.NotIn)
                    else z.Exists([index], z.And(inside, equal)))
        if isinstance(left, Maximum) or isinstance(right, Maximum):
            if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                return self._maximum_order(op, left, right)
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                raise Z3FormulaError("Unsupported maximum comparison operator.")
            if isinstance(left, Maximum) and isinstance(right, Maximum):
                common = z.FreshInt("common_maximum")
                equal = z.Exists([common], z.And(self._maximum_equals(left, common), self._maximum_equals(right, common)))
                return z.Not(equal) if isinstance(op, ast.NotEq) else equal
            maximum, value = (left, right) if isinstance(left, Maximum) else (right, left)
            equal = self._maximum_equals(maximum, value)
            return z.Not(equal) if isinstance(op, ast.NotEq) else equal
        if isinstance(left, ArrayView) or isinstance(right, ArrayView):
            if not isinstance(left, ArrayView) or not isinstance(right, ArrayView) or not isinstance(op, (ast.Eq, ast.NotEq)):
                raise Z3FormulaError("Array comparisons support equality only.")
            index = z.FreshInt("equal")
            value = z.And(left.length == right.length, z.ForAll([index], z.Implies(
                z.And(index >= 0, index < left.length),
                z.Select(left.data, left.start + index) == z.Select(right.data, right.start + index))))
            return z.Not(value) if isinstance(op, ast.NotEq) else value
        return self._compare(op, left, right)

    def _maximum_order(self, op, left, right):
        z = self.z3
        if isinstance(left, Maximum) and isinstance(right, Maximum):
            if isinstance(op, (ast.Gt, ast.GtE)):
                left, right = right, left
                op = ast.Lt() if isinstance(op, ast.Gt) else ast.LtE()
            li, ri = z.FreshInt("left_maximum"), z.FreshInt("right_maximum")
            a, b = left.array, right.array
            relation = self._compare(op, z.Select(a.data, a.start + li), z.Select(b.data, b.start + ri))
            # Every left element is bounded by some right element; both arrays must be nonempty.
            return z.ForAll([li], z.Implies(z.And(li >= 0, li < a.length),
                z.Exists([ri], z.And(ri >= 0, ri < b.length, relation))))
        if isinstance(right, Maximum):
            reverse = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE}
            left, right, op = right, left, reverse[type(op)]()
        index, array = z.FreshInt("maximum_order"), left.array
        inside = z.And(index >= 0, index < array.length)
        relation = self._compare(op, z.Select(array.data, array.start + index), right)
        if isinstance(op, (ast.Lt, ast.LtE)):
            return z.ForAll([index], z.Implies(inside, relation))
        return z.Exists([index], z.And(inside, relation))

    def _maximum_equals(self, maximum, value):
        z = self.z3
        array = maximum.array
        index = z.FreshInt("maximum")
        inside = z.And(index >= 0, index < array.length)
        return z.And(z.ForAll([index], z.Implies(inside, z.Select(array.data, array.start + index) <= value)),
                     z.Exists([index], z.And(inside, z.Select(array.data, array.start + index) == value)))

    def _statements(self, statements, state):
        z = self.z3
        state, checks = dict(state), []
        for stmt in statements:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                value, safe = self._term(stmt.value, state)
                if isinstance(value, (ArrayView, Maximum)) or isinstance(state.get(stmt.targets[0].id), ArrayView):
                    raise Z3FormulaError("Symbolic array mutation/aliasing is not implemented.")
                state[stmt.targets[0].id] = value
                checks.append(safe)
            elif (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                  and isinstance(stmt.targets[0], ast.Subscript)
                  and isinstance(stmt.targets[0].value, ast.Name)
                  and not isinstance(stmt.targets[0].slice, ast.Slice)):
                target = stmt.targets[0]
                array = state[target.value.id]
                if not isinstance(array, ArrayView):
                    raise Z3FormulaError("Array write requires an integer-array variable.")
                index, idf = self._term(target.slice, state)
                value, vd = self._term(stmt.value, state)
                if isinstance(value, (ArrayView, Maximum)):
                    raise Z3FormulaError("Array elements must be integers.")
                state[target.value.id] = ArrayView(z.Store(array.data, array.start + index, value), array.length, array.start)
                checks.append(z.And(idf, vd, index >= 0, index < array.length))
            elif isinstance(stmt, ast.If):
                condition, cd = self._term(stmt.test, state)
                yes, yd = self._statements(stmt.body, state)
                no, nd = self._statements(stmt.orelse, state)
                checks.extend([cd, z.Implies(condition, yd), z.Implies(z.Not(condition), nd)])
                for key in state:
                    if isinstance(state[key], ArrayView):
                        a, b = yes[key], no[key]
                        state[key] = ArrayView(z.If(condition, a.data, b.data), a.length, a.start)
                    else:
                        state[key] = z.If(condition, yes[key], no[key])
            else:
                raise Z3FormulaError("Array proofs support scalar assignments and if branches, not nested loops or array writes.")
        return state, z.And(*checks)

    def _wp(self, statements, state, post):
        """Demonic weakest precondition; inner-loop exits are universally quantified."""
        z = self.z3
        if not statements:
            return post(state)
        first, rest = statements[0], statements[1:]
        continuation = lambda after: self._wp(rest, after, post)
        if isinstance(first, ast.If):
            condition, defined = self._term(first.test, state)
            return z.And(defined,
                z.Implies(condition, self._wp(first.body, state, continuation)),
                z.Implies(z.Not(condition), self._wp(first.orelse, state, continuation)))
        if isinstance(first, ast.While):
            index, annotation = infer_scan_annotation(first)
            if annotation not in self.scan_annotations:
                self.scan_annotations.append(annotation)
            initial, initial_defined = self._expression(annotation, state)
            arbitrary = z.FreshInt("scan_head")
            head = dict(state, **{index: arbitrary})
            invariant, invariant_defined = self._expression(annotation, head)
            guard, guard_defined = self._term(first.test, head)
            def preserved(after):
                value, defined = self._expression(annotation, after)
                return z.And(defined, value)
            return z.And(initial_defined, initial,
                z.ForAll([arbitrary], z.Implies(z.And(invariant_defined, invariant),
                    z.And(guard_defined,
                        z.Implies(guard, self._wp(first.body, head, preserved)),
                        z.Implies(z.Not(guard), continuation(head))))))
        after, safe = self._statements([first], state)
        return z.And(safe, continuation(after))

    def verify_program(self, program, precondition, invariant, postcondition, array_names=("A",), *, proof_context=None):
        context = proof_context or TextbookProofContext(version="standalone_v1")
        try:
            allowed = {ast.dump(ast.parse(text, mode="eval")) for text in preserved_assumptions(program, precondition)}
            if any(ast.dump(ast.parse(text, mode="eval")) not in allowed for text in context.global_assumptions):
                raise ValueError("Proof context contains a fact that is not an unchanged precondition conjunct.")
        except (SyntaxError, ValueError) as exc:
            return VerificationResult(invariant, False, "error", "not checked", "not checked",
                                      error=str(exc), verification_contract=context.version)
        effective = context.effective_invariant(invariant)
        result = self._verify_program(program, precondition, effective, postcondition, array_names)
        contribution = "not_assessed"
        if result.is_inductive and context.source_contract_applied:
            contribution = self._context_contribution(program, invariant, context, array_names)
        return replace(result, candidate=invariant, effective_candidate=effective,
                       candidate_contribution=contribution,
                       verification_contract=context.version, proof_context=asdict(context),
                       hoare_steps=[
                           f"Verification contract: {context.version}.",
                           "Preserved input assumptions: " + ("; ".join(context.global_assumptions) or "none"),
                           f"Supplied domain bounds (proved jointly, not trusted): {context.loop_head_bounds}.",
                           f"Effective invariant I: {effective}",
                       ] + result.hoare_steps)

    def _context_contribution(self, program, candidate, context, array_names):
        z = self.z3
        names = {node.id for node in ast.walk(ast.parse(program)) if isinstance(node, ast.Name)}
        state = {name: ArrayView(z.Array(name, z.IntSort(), z.IntSort()), z.FreshInt(f"length_{name}"))
                 if name in array_names else z.Int(name) for name in names}
        domain = z.And(*(value.length >= 0 for value in state.values() if isinstance(value, ArrayView)))
        cv, cd = self._expression(context.effective_invariant("True"), state)
        iv, defined = self._expression(candidate, state)
        implication = self._prove_implies(z.And(domain, cd, cv), z.And(defined, iv))
        return {"proved": "context_entailed", "failed": "nontrivial"}.get(implication.status, "unknown")

    def _verify_program(self, program, precondition, invariant, postcondition, array_names):
        z = self.z3
        self.scan_annotations = []
        try:
            prefix, loop, suffix = split_program(program)
            if loop.orelse:
                raise Z3FormulaError("while-else is unsupported.")
            functions = {"len", "max", "all", "any", "range"}
            tree = ast.parse(program)
            # Specifications may use the program's inputs, not arbitrary invented variables.
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} - functions
            state = {name: ArrayView(z.Array(name, z.IntSort(), z.IntSort()), z.FreshInt(f"length_{name}"))
                     if name in array_names else z.Int(name) for name in sorted(names)}
            # Check program capabilities before attributing a candidate failure to the model.
            try:
                initialized, init_safe = self._statements(prefix, state)
                guard, gd = self._term(loop.test, state)
                self._wp(loop.body, state, lambda after: z.BoolVal(True))
            except (Z3FormulaError, TypeError, z.Z3Exception) as exc:
                return VerificationResult(invariant, False, "unsupported", "unsupported", "unsupported",
                                          error=str(exc), limitation_scope="program")
            try:
                ast.parse(invariant, mode="eval")
            except SyntaxError as exc:
                return VerificationResult(invariant, False, "invalid", "not checked", "not checked", error=str(exc))
            def specification_names(text):
                expression = ast.parse(text, mode="eval")
                calls = {node.func.id for node in ast.walk(expression)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
                return free_names(expression) - calls
            if specification_names(precondition) - names - functions:
                return VerificationResult(invariant, False, "error", "not checked", "not checked",
                                          error="Precondition uses undeclared variables.")
            if specification_names(invariant) - names - functions:
                return VerificationResult(invariant, False, "invalid", "not checked", "not checked",
                                          error="Candidate uses undeclared variables.")
            domain = z.And(*(value.length >= 0 for value in state.values() if isinstance(value, ArrayView)))
            pv, pd = self._expression(precondition, state)
            initial = z.And(domain, pd, pv)
            feasible = z.Solver()
            feasible.set(timeout=self.timeout_ms)
            feasible.add(initial)
            decision = feasible.check()
            if decision != z.sat:
                return VerificationResult(invariant, False, "infeasible" if decision == z.unsat else "unknown",
                                          "not checked", "not checked", error="Precondition feasibility not established.")
            try:
                iv0, id0 = self._expression(invariant, initialized)
                iv, idef = self._expression(invariant, state)
                def invariant_after(after):
                    value, defined = self._expression(invariant, after)
                    return z.And(defined, value)
                preserved_body = self._wp(loop.body, state, invariant_after)
            except (Z3FormulaError, TypeError, z.Z3Exception) as exc:
                return VerificationResult(invariant, False, "unsupported", "unsupported", "unsupported",
                                          error=str(exc), limitation_scope="candidate_expression")
            def witness_state(values):
                return {(f"len({key})" if isinstance(value, ArrayView) else key):
                        (value.length if isinstance(value, ArrayView) else value) for key, value in values.items()}
            establishment = self._prove_implies(initial, z.And(init_safe, id0, iv0),
                {"initial": witness_state(state), "loop_head": witness_state(initialized)})
            preservation = self._prove_implies(z.And(domain, idef, iv),
                z.And(gd, z.Implies(guard, preserved_body)),
                {"before": witness_state(state)})
            try:
                final, final_safe = self._statements(suffix, state)
                qv, qd = self._expression(postcondition, final)
                exit_check = self._prove_implies(z.And(domain, idef, iv, gd, z.Not(guard)),
                    z.And(final_safe, qd, qv), {"exit": witness_state(state), "final": witness_state(final)})
            except (Z3FormulaError, SyntaxError, ValueError, TypeError, z.Z3Exception) as exc:
                exit_check = ObligationCheck("postcondition", "unsupported", diagnostic=str(exc))
            checks = (establishment, preservation, exit_check)
            return VerificationResult(invariant, all(check.status == "proved" for check in checks),
                *(check.status for check in checks),
                error="; ".join(check.diagnostic for check in checks if check.diagnostic),
                hoare_steps=[
                    "Arrays have symbolic integer elements and arbitrary nonnegative lengths; no trace constants are proof assumptions.",
                    "Array indices must be nonnegative and within bounds. Boolean predicates use short-circuit definedness.",
                    "Initialization: " + "; ".join(ast.unparse(stmt) for stmt in prefix),
                    "Guard: " + ast.unparse(loop.test),
                    "Loop body: " + "; ".join(ast.unparse(stmt) for stmt in loop.body),
                    "Post-loop statements: " + ("; ".join(ast.unparse(stmt) for stmt in suffix) or "none"),
                    f"Establishment: P => safe(prefix) and I(after prefix): {establishment.status}.",
                    f"Preservation: I => safe(guard) and (guard => safe(body) and I(after body)): {preservation.status}.",
                    f"Exit: I and not guard => safe(suffix) and Q(after suffix): {exit_check.status}.",
                    "This is partial correctness, not a proof of termination.",
                    "Counterexamples list scalar states and array lengths; array contents are not expanded.",
                    "Natural powers use sound ground identities; square-law base/induction steps are Z3-certified. SAT power abstractions remain inconclusive.",
                ] + ["Inferred inner-loop annotation (initialization, safety, preservation and exit checked jointly): " + text
                     for text in self.scan_annotations], limitation_scope="postcondition" if exit_check.status == "unsupported" else "",
                counterexamples={name: check.counterexample for name, check in zip(
                    ("establishment", "preservation", "postcondition"), checks) if check.counterexample is not None})
        except (Z3FormulaError, SyntaxError, ValueError, TypeError, z.Z3Exception) as exc:
            return VerificationResult(invariant, False, "unsupported", "unsupported", "unsupported",
                                      error=str(exc), limitation_scope="program_or_specification")
