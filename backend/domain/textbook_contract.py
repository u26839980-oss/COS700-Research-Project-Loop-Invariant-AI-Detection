"""Explicit, reproducible proof context shared by textbook prompts and checks."""
from __future__ import annotations

import ast
from dataclasses import dataclass


CONTRACT_VERSION = "textbook_context_v2"


def resolve_unassisted_context(program: str, precondition: str) -> TextbookProofContext:
    """Frame input facts only; never inject source-derived invariant bounds."""
    return TextbookProofContext(
        global_assumptions=preserved_assumptions(program, precondition),
        loop_head_bounds="True", source_contract_applied=True,
        version="textbook_unassisted_v1",
    )


def written_names(program: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(program)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            root = node.value
            while isinstance(root, ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                names.add(root.id)
    return names


def free_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
        return free_names(node.body if node.test.value else node.orelse)
    if isinstance(node, ast.Name):
        return {node.id} if isinstance(node.ctx, ast.Load) else set()
    if isinstance(node, ast.GeneratorExp):
        names, bound = set(), set()
        for clause in node.generators:
            names |= free_names(clause.iter) - bound
            bound |= {item.id for item in ast.walk(clause.target) if isinstance(item, ast.Name)}
            for condition in clause.ifs:
                names |= free_names(condition) - bound
        return names | (free_names(node.elt) - bound)
    return set().union(*(free_names(child) for child in ast.iter_child_nodes(node)))


def preserved_assumptions(program: str, precondition: str) -> tuple[str, ...]:
    modified = written_names(program)
    tree = ast.parse(program)
    rebound = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    # Element writes change contents, not length. Replacement/slice writes are not framed.
    element_only = modified - rebound
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Slice):
            element_only -= free_names(node.value)
    def dependencies(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
                and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Name)
                and node.args[0].id in element_only):
            return set()
        if isinstance(node, ast.GeneratorExp):
            return free_names(node)
        if isinstance(node, ast.Name):
            return free_names(node)
        return set().union(*(dependencies(child) for child in ast.iter_child_nodes(node)))
    def conjuncts(node):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            return [part for child in node.values for part in conjuncts(child)]
        return [node]
    return tuple(ast.unparse(node) for node in conjuncts(ast.parse(precondition, mode="eval").body)
                 if not dependencies(node) & modified)


@dataclass(frozen=True)
class TextbookProofContext:
    global_assumptions: tuple[str, ...] = ()
    loop_head_bounds: str = "True"
    source_contract_applied: bool = False
    version: str = CONTRACT_VERSION

    def effective_invariant(self, candidate: str) -> str:
        return " and ".join(f"({part})" for part in (*self.global_assumptions, self.loop_head_bounds, candidate))


def resolve_textbook_context(benchmark: dict, program: str, precondition: str,
                             postcondition: str) -> TextbookProofContext:
    canonical = benchmark.get("program", benchmark.get("program_text", ""))
    def same(left, right, mode="exec"):
        return bool(left and right) and ast.dump(ast.parse(left, mode=mode)) == ast.dump(ast.parse(right, mode=mode))
    source_applies = (same(program, canonical)
                      and same(precondition, benchmark.get("precondition", ""), "eval")
                      and same(postcondition, benchmark.get("postcondition", ""), "eval"))
    return TextbookProofContext(
        global_assumptions=preserved_assumptions(program, precondition),
        loop_head_bounds=benchmark.get("loop_head_bounds", "True") if source_applies else "True",
        source_contract_applied=source_applies,
    )
