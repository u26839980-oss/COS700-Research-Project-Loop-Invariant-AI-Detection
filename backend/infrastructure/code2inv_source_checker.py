"""Check the upstream Code2Inv SMT-Int VCs, not a guessed C translation.

The archived template is authoritative for this adapter. Its mathematical integer
semantics must not be presented as a proof of native machine-width C arithmetic.
"""
from __future__ import annotations

import ast

import sexpdata

from backend.domain.models import VerificationResult
from backend.infrastructure.z3_checker import Z3FormulaError, Z3InvariantChecker


MARKER = "SPLIT_HERE_asdfghjklzxcvbnmqwertyuiop"
CONTRACT = "code2inv_upstream_smt_int_v1"


class Code2InvSourceChecker(Z3InvariantChecker):
    def __init__(self, template: str, timeout_ms=5000):
        super().__init__(timeout_ms)
        self.parts = template.split(MARKER)
        if len(self.parts) != 5:
            raise Z3FormulaError("Expected the five-part upstream Code2Inv template.")
        forms = sexpdata.loads("(" + self.parts[0] + "true" + "".join(self.parts[1:]) + "\n)")
        definitions = [form for form in forms if isinstance(form, list) and str(form[0]) == "define-fun"]
        inv = [form for form in definitions if str(form[1]) == "inv-f"]
        if len(inv) != 1 or str(inv[0][3]) != "Bool":
            raise Z3FormulaError("Expected one Boolean inv-f definition.")
        signature = inv[0][2]
        if not signature or any(str(kind) != "Int" for _, kind in signature):
            raise Z3FormulaError("This adapter only supports upstream integer-state templates.")
        self.symbols = {str(name): self.z3.Int(str(name)) for name, _ in signature}
        # Parsing the original counterexample queries also validates each SMT fragment.
        self.queries("true")
        uf_forms = []
        for form in forms:
            if isinstance(form, list) and str(form[0]) == "assert":
                continue
            if isinstance(form, list) and str(form[0]) == "define-fun" and str(form[1]) == "inv-f":
                uf_forms.append([sexpdata.Symbol("declare-fun"), sexpdata.Symbol("inv-f"),
                                 [kind for _, kind in signature], sexpdata.Symbol("Bool")])
            else:
                uf_forms.append(form)
        prefix = "\n".join(sexpdata.dumps(form) for form in uf_forms)
        self.horn_obligations = []
        # Parse together: separate SMT parser calls create different predicate declarations.
        parsed = self.z3.parse_smt2_string(prefix + "\n" + "\n".join(self.parts[2:]))
        if len(parsed) != 3:
            raise Z3FormulaError("Expected three upstream counterexample queries.")
        for query in parsed:
            if not self.z3.is_not(query) or not self.z3.is_implies(query.arg(0)):
                raise Z3FormulaError("Unexpected upstream obligation structure.")
            self.horn_obligations.append(query.arg(0))
        predicates = {}
        def collect(node):
            if self.z3.is_app(node) and str(node.decl().name()) == "inv-f":
                predicates[node.decl().get_id()] = node.decl()
            for child in node.children():
                collect(child)
        for obligation in self.horn_obligations:
            collect(obligation)
        if len(predicates) != 1:
            raise Z3FormulaError("Source obligations do not share one invariant predicate.")
        self.inv = next(iter(predicates.values()))

    def queries(self, body):
        prefix = self.parts[0] + body + self.parts[1]
        queries = []
        for fragment in self.parts[2:]:
            parsed = self.z3.parse_smt2_string(prefix + fragment)
            if len(parsed) != 1:
                raise Z3FormulaError("Each source query must contain exactly one assertion.")
            queries.append(parsed[0])
        return queries

    def verify_expression(self, expression, *, label=None):
        from z3.z3util import get_vars
        z = self.z3
        if not z.is_bool(expression) or any(str(variable) not in self.symbols for variable in get_vars(expression)):
            raise Z3FormulaError("Certificate contains undeclared state variables or is not Boolean.")
        queries = self.queries(expression.sexpr())
        feasible = z.Solver()
        feasible.set(timeout=self.timeout_ms)
        feasible.add(self.horn_obligations[0].arg(0))
        decision = feasible.check()
        if decision != z.sat:
            return VerificationResult(str(expression), False, "infeasible" if decision == z.unsat else "unknown",
                "not checked", "not checked", error="Source precondition feasibility not established.", verification_contract=CONTRACT)
        checks = [self._prove_implies(z.BoolVal(True), z.Not(query)) for query in queries]
        return VerificationResult(label or expression.sexpr(), all(check.status == "proved" for check in checks),
            *(check.status for check in checks), verification_contract=CONTRACT,
            effective_candidate=expression.sexpr(),
            error="; ".join(check.diagnostic for check in checks if check.diagnostic),
            counterexamples={name: check.counterexample for name, check in zip(
                ("establishment", "preservation", "postcondition"), checks) if check.counterexample is not None},
            hoare_steps=["Authoritative semantics: the pinned upstream SMT mathematical-integer verification conditions.",
                "No native C overflow/memory or termination claim. No source-derived invariant bounds are injected.",
                "Each of the three original counterexample queries must independently be UNSAT."])

    def verify(self, candidate):
        try:
            tree = ast.parse(candidate, mode="eval")
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            if names - self.symbols.keys():
                raise Z3FormulaError("Candidate uses variables outside the upstream invariant signature.")
            expression = self._expr_ast(tree.body, dict(self.symbols))
            return self.verify_expression(expression, label=candidate)
        except (SyntaxError, TypeError, ValueError, self.z3.Z3Exception) as exc:
            return VerificationResult(candidate, False, "unsupported", "unsupported", "unsupported",
                error=str(exc), verification_contract=CONTRACT, limitation_scope="candidate_expression")

    def initial_bad_state(self):
        """Find a zero-transition violation in the exact source SMT state space."""
        z = self.z3
        solver = z.Solver()
        solver.set(timeout=self.timeout_ms)
        solver.add(self.horn_obligations[0].arg(0), z.Not(self.horn_obligations[2].arg(1)))
        decision = solver.check()
        result = dict(status=str(decision), scope=CONTRACT, transitions=0)
        if decision == z.sat:
            state = self.horn_obligations[0].arg(1).children()
            result["state"] = {name: str(solver.model().eval(variable, model_completion=True))
                               for name, variable in zip(self.symbols, state)}
        elif decision == z.unknown:
            result["reason"] = solver.reason_unknown()
        return result

    def infer_baseline(self, timeout_ms=2000):
        """Spacer proposes a certificate; ordinary source-VC checks must validate it."""
        from z3.z3util import get_vars
        z = self.z3
        solver = z.SolverFor("HORN")
        solver.set(timeout=timeout_ms)
        for obligation in self.horn_obligations:
            variables = get_vars(obligation)
            solver.add(z.ForAll(variables, obligation) if variables else obligation)
        decision = solver.check()
        if decision != z.sat:
            return dict(generator="Z3 HORN/Spacer; not a model and not an upstream reference invariant",
                status="vc_unsatisfiable" if decision == z.unsat else "unknown", reason=solver.reason_unknown() if decision == z.unknown else "",
                certificate=None)
        # Keep state variables symbolic; completion would replace them by arbitrary constants.
        expression = z.simplify(solver.model().eval(self.inv(*self.symbols.values()), model_completion=False))
        checked = self.verify_expression(expression)
        return dict(generator="Z3 HORN/Spacer; independently rechecked against all three original queries",
            status="certificate_verified" if checked.accepted else "certificate_not_verified",
            certificate=expression.sexpr(), verification=checked.to_record())
