"""Seeded walks in the original Code2Inv SMT-Int relations; no gold postconditions."""
from __future__ import annotations

import random
import time

import z3

from backend.infrastructure.code2inv_source_checker import Code2InvSourceChecker


class Code2InvTraceSampler:
    def sample(self, template, options):
        checker = Code2InvSourceChecker(template)
        initial = checker.horn_obligations[0]
        step = checker.horn_obligations[1]
        current, following = list(initial.arg(1).children()), list(step.arg(1).children())
        relation = step.arg(0)
        if not z3.is_and(relation) or len(relation.children()) != 2:
            raise ValueError("Unexpected upstream transition structure")
        transition = relation.arg(1)
        names = list(checker.symbols)
        started = time.monotonic()
        deadline = started + options.timeout_seconds
        runs, initial_states = [], []
        total_queries = 0

        def solve(formula, seed):
            nonlocal total_queries
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "sampling_timeout"
            # Global AST/solver history can affect model choice even with fixed seeds.
            # Isolate each query so changing another task/budget cannot change this path.
            context = z3.Context()
            solver = z3.Solver(ctx=context)
            solver.set(timeout=max(1, int(remaining * 1000)), random_seed=seed % 2147483647)
            solver.add(formula.translate(context))
            total_queries += 1
            decision = solver.check()
            return (solver.model(), "sat") if decision == z3.sat else (None, str(decision))

        def values(model, variables):
            return [model.eval(variable.translate(model.ctx), model_completion=True).as_long() for variable in variables]

        for sample_index in range(options.samples):
            deadline = time.monotonic() + options.timeout_seconds
            seed = options.seed + sample_index
            rng = random.Random(seed)
            precondition = initial.arg(0)
            blocking = z3.And([z3.Or([variable != value for variable, value in zip(current, state)]) for state in initial_states])
            model, status = solve(z3.And(precondition, blocking), seed)
            duplicate_initial = False
            if status == "unsat":
                model, status = solve(precondition, seed)
                duplicate_initial = True
            if model is None:
                runs.append(dict(sample_index=sample_index, seed=seed, status=status, observations=[]))
                continue
            constraints = [precondition] + ([] if duplicate_initial else [blocking])
            # Generic small-value preferences improve diversity, never constrain the task.
            for variable in current:
                palette = [-10, -5, -1, 0, 1, 5, 10, 25, 100]
                rng.shuffle(palette)
                for value in palette[:3]:
                    proposal, proposal_status = solve(z3.And(*constraints, variable == value), seed)
                    if proposal_status == "sat":
                        constraints.append(variable == value)
                        model = proposal
                        break
                    if proposal_status not in {"unsat", "sat"}:
                        break
            state = values(model, current)
            initial_states.append(state)
            observations = [dict(loop_index="0", visit=0, event_index=0, state=dict(zip(names, state)))]
            status = "execution_step_budget" if options.execution_steps + 1 < options.rows_per_loop else "prefix_budget_reached"
            for index in range(1, min(options.rows_per_loop, options.execution_steps + 1)):
                fixed = z3.And([variable == value for variable, value in zip(current, state)])
                changed = z3.Or([a != b for a, b in zip(current, following)])
                model, decision = solve(z3.And(transition, fixed, changed), seed + index)
                if decision == "unsat":
                    model, fallback = solve(z3.And(transition, fixed), seed + index)
                    status = "stutter_only" if fallback == "sat" else "no_transition" if fallback == "unsat" else fallback
                    break
                if model is None:
                    status = decision
                    break
                state = values(model, following)
                observations.append(dict(loop_index="0", visit=index, event_index=index, state=dict(zip(names, state))))
            runs.append(dict(sample_index=sample_index, seed=seed, status=status, observations=observations,
                             duplicate_initial=duplicate_initial))
        return dict(backend="upstream_SMT_Int", runs=runs, solver_queries=total_queries,
            sampling_policy="Seeded satisfiability walks, small-value input preferences, changing successors preferred. Not uniform or exhaustive.",
            observation_semantics="SMT relation states, not native C executions. Stutter-only is NOT a termination proof.",
            duration_seconds=time.monotonic() - started, model_calls=0)


def validate_smt_observations(template, runs):
    """Independently recheck recorded initial states and every adjacent transition."""
    checker = Code2InvSourceChecker(template)
    pre, step = checker.horn_obligations[:2]
    current, following = pre.arg(1).children(), step.arg(1).children()
    names = list(checker.symbols)
    checked = 0
    for run in runs:
        rows = run["observations"]
        if not rows:
            continue
        solver = z3.Solver()
        solver.set(timeout=5000)
        solver.add(pre.arg(0), *[variable == rows[0]["state"][name] for name, variable in zip(names, current)])
        if solver.check() != z3.sat:
            raise ValueError("Recorded initial state does not satisfy the original source relation")
        checked += 1
        for left, right in zip(rows, rows[1:]):
            solver = z3.Solver()
            solver.set(timeout=5000)
            solver.add(step.arg(0).arg(1),
                *[variable == left["state"][name] for name, variable in zip(names, current)],
                *[variable == right["state"][name] for name, variable in zip(names, following)])
            if solver.check() != z3.sat:
                raise ValueError("Recorded transition does not satisfy the original source relation")
            checked += 1
    return checked
