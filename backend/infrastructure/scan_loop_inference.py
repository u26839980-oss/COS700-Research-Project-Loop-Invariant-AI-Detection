"""Infer an annotation from a bounded, unit-step, read-only scan's syntax.

Inference is not proof: the caller must discharge initialization, preservation,
guard safety and exit-to-continuation obligations for the returned annotation.
"""
from __future__ import annotations

import ast
from copy import deepcopy

from backend.infrastructure.z3_checker import Z3FormulaError


def infer_scan_annotation(loop: ast.While) -> tuple[str, str]:
    if loop.orelse or not isinstance(loop.test, ast.BoolOp) or not isinstance(loop.test.op, ast.And):
        raise Z3FormulaError("Nested loops require a bounded unit-step scan guard.")
    guard = loop.test.values[0]
    if not (isinstance(guard, ast.Compare) and isinstance(guard.left, ast.Name)
            and len(guard.ops) == 1 and isinstance(guard.ops[0], (ast.NotEq, ast.Lt))):
        raise Z3FormulaError("Nested scan must test index != bound or index < bound first.")
    index, bound = guard.left.id, ast.unparse(guard.comparators[0])
    expected = ast.parse(f"{index} = {index} + 1").body
    if ast.dump(ast.Module(body=loop.body, type_ignores=[])) != ast.dump(ast.Module(body=expected, type_ignores=[])):
        raise Z3FormulaError("Nested scan body must only increment its index by one.")
    if index in {node.id for node in ast.walk(guard.comparators[0]) if isinstance(node, ast.Name)}:
        raise Z3FormulaError("Nested scan bound must not depend on its index.")
    predicate = ast.BoolOp(op=ast.And(), values=deepcopy(loop.test.values[1:]))
    # A fresh binder prevents accidental capture of program variables.
    names = {node.id for node in ast.walk(loop) if isinstance(node, ast.Name)}
    binder = "scan_position"
    while binder in names:
        binder += "_"
    class Substitute(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.copy_location(ast.Name(id=binder, ctx=ast.Load()), node) if node.id == index else node
    predicate = ast.fix_missing_locations(Substitute().visit(predicate))
    annotation = f"0 <= {index} <= ({bound}) and all(({ast.unparse(predicate)}) for {binder} in range({index}))"
    return index, annotation
