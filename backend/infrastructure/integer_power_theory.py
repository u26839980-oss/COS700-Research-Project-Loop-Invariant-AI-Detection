"""Sound ground consequences of natural integer exponentiation.

The uninterpreted function is an over-approximation, not a decision procedure:
UNSAT proves an obligation; SAT alone cannot refute one involving this function.
No candidate-specific equations or finite execution bounds are assumed.
"""
from __future__ import annotations


class IntegerPowerTheory:
    def __init__(self, z3):
        self.z = z3
        self.power = z3.Function("natural_integer_power", z3.IntSort(), z3.IntSort(), z3.IntSort())

    def terms(self, *expressions):
        found, seen = [], set()
        def visit(node):
            if node.get_id() in seen:
                return
            seen.add(node.get_id())
            if self.z.is_quantifier(node):
                # Ground instances must never leak bound variables out of their scope.
                return
            if self.z.is_app(node) and node.decl() == self.power:
                found.append(node)
            for child in node.children():
                visit(child)
        for expression in expressions:
            visit(expression)
        return found

    def facts(self, terms):
        z, facts = self.z, []
        for term in terms:
            base, exponent = term.children()
            facts.extend((z.Implies(exponent == 0, term == 1),
                          z.Implies(exponent == 1, term == base),
                          z.Implies(z.And(base == 0, exponent > 0), term == 0),
                          z.Implies(z.And(base == 1, exponent >= 0), term == 1)))
            for other in terms:
                other_base, other_exponent = other.children()
                # The successor equation is the definition of natural powers.
                facts.append(z.Implies(z.And(base == other_base, other_exponent >= 0,
                                             exponent == other_exponent + 1), term == base * other))
                # (b*b)^k = b^(2*k), for natural k; see induction certificate below.
                if z.is_mul(base) and base.num_args() == 2 and z.eq(base.arg(0), base.arg(1)):
                    facts.append(z.Implies(z.And(base.arg(0) == other_base, exponent >= 0,
                                                 other_exponent == 2 * exponent), term == other))
        return facts

    def concrete_counterexample(self, premise, conclusion, timeout_ms):
        """Certify a SAT witness by fixing scalars and evaluating every power exactly.

        Arrays/quantifiers may remain symbolic, but a power depending on either
        cannot be concretized and is left inconclusive. This is refutation only.
        """
        z = self.z
        solver = z.Solver()
        solver.set(timeout=timeout_ms)
        solver.add(premise, *self.facts(self.terms(premise, conclusion)), z.Not(conclusion))
        if solver.check() != z.sat:
            return None
        model, constants = solver.model(), {}
        def collect(node):
            if z.is_const(node) and node.decl().kind() == z.Z3_OP_UNINTERPRETED and node.sort() == z.IntSort():
                constants[node.get_id()] = node
            for child in node.children():
                collect(child)
        collect(premise)
        collect(conclusion)
        substitutions = [(item, model.eval(item, model_completion=True)) for item in constants.values()]
        def evaluate(node):
            if z.is_quantifier(node):
                # Substitution underneath quantifiers is safe only for ground power terms.
                replacements = []
                def inspect(child):
                    if z.is_app(child) and child.decl() == self.power:
                        replacements.append((child, evaluate(child)))
                    else:
                        for part in child.children():
                            inspect(part)
                inspect(node.body())
                return z.substitute(node, *replacements) if replacements else node
            if z.is_app(node) and node.decl() == self.power:
                base, exponent = [z.simplify(evaluate(child)) for child in node.children()]
                if not (z.is_int_value(base) and z.is_int_value(exponent)):
                    raise ValueError("Non-ground power")
                b, e = base.as_long(), exponent.as_long()
                if not (0 <= e <= 10000) or abs(b).bit_length() * e > 65536:
                    raise ValueError("Concrete counterexample power exceeds diagnostic budget")
                return z.IntVal(pow(b, e))
            children = node.children()
            return z.substitute(node, *((child, evaluate(child)) for child in children)) if children else node
        try:
            before = evaluate(z.substitute(premise, *substitutions))
            after = evaluate(z.substitute(conclusion, *substitutions))
        except ValueError:
            return None
        exact = z.Solver()
        exact.set(timeout=timeout_ms)
        exact.add(before, z.Not(after))
        if exact.check() != z.sat:
            return None
        return {str(item): str(value) for item, value in substitutions}

    def square_induction_certificate(self):
        """Check base/step of the square law using only defining equations and IH."""
        z, power = self.z, self.power
        b, k = z.Ints("power_certificate_base power_certificate_index")
        base = z.Solver()
        base.add(power(b * b, 0) == 1, power(b, 0) == 1,
                 power(b * b, 0) != power(b, 0))
        step = z.Solver()
        step.add(k >= 0, power(b * b, k) == power(b, 2 * k),
                 power(b * b, k + 1) == (b * b) * power(b * b, k),
                 power(b, 2 * k + 1) == b * power(b, 2 * k),
                 power(b, 2 * k + 2) == b * power(b, 2 * k + 1),
                 power(b * b, k + 1) != power(b, 2 * k + 2))
        base.set(timeout=1000)
        step.set(timeout=1000)
        return base.check() == z.unsat and step.check() == z.unsat
