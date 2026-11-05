from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterable


SUPPORTED_EXPR_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


SUPPORTED_STMT_NODES = (
    ast.Module,
    ast.Assign,
    ast.AugAssign,
    ast.If,
    ast.While,
    ast.Name,
    ast.Store,
    ast.Load,
    ast.Expr,
    ast.Constant,
    ast.BinOp,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


@dataclass(frozen=True)
class ParsedLoopProgram:
    tree: ast.Module
    prefix: list[ast.stmt]
    loop: ast.While
    variables: list[str]


class LoopParseError(ValueError):
    pass


def parse_program(program_text: str) -> ParsedLoopProgram:
    try:
        tree = ast.parse(program_text)
    except SyntaxError as exc:
        raise LoopParseError(f"Program syntax error: {exc}") from exc

    _validate_nodes(tree)
    loops = [stmt for stmt in tree.body if isinstance(stmt, ast.While)]
    if len(loops) != 1:
        raise LoopParseError("The first prototype expects exactly one top-level while loop.")

    loop = loops[0]
    if loop.orelse or sum(isinstance(node, ast.While) for node in ast.walk(tree)) != 1:
        raise LoopParseError("Nested loops and while-else are not supported.")
    loop_index = tree.body.index(loop)
    prefix = tree.body[:loop_index]
    suffix = tree.body[loop_index + 1 :]
    if suffix:
        raise LoopParseError("Statements after the while loop are not supported yet.")

    variables = sorted(_collect_names(tree))
    return ParsedLoopProgram(tree=tree, prefix=prefix, loop=loop, variables=variables)


def parse_expression(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise LoopParseError(f"Expression syntax error in '{expression}': {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Div):
            raise LoopParseError("Floating-point / is outside the integer subset; use //.")
        if not isinstance(node, SUPPORTED_EXPR_NODES):
            raise LoopParseError(f"Unsupported expression syntax: {type(node).__name__}")
    return tree


def extract_syntax_features(parsed: ParsedLoopProgram) -> dict[str, object]:
    assigned_before_loop = _assigned_names(parsed.prefix)
    assigned_in_loop = _assigned_names(parsed.loop.body)
    return {
        "variables": parsed.variables,
        "initialisation": [_unparse(stmt) for stmt in parsed.prefix],
        "guard": _unparse(parsed.loop.test),
        "body": [_unparse(stmt) for stmt in parsed.loop.body],
        "modified_before_loop": sorted(assigned_before_loop),
        "modified_inside_loop": sorted(assigned_in_loop),
    }


def expression_names(expressions: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for expression in expressions:
        if not expression.strip():
            continue
        tree = parse_expression(expression)
        names.update(_collect_names(tree))
    return names


def _validate_nodes(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, SUPPORTED_STMT_NODES):
            raise LoopParseError(f"Unsupported program syntax: {type(node).__name__}")
        if isinstance(node, ast.Assign) and len(node.targets) != 1:
            raise LoopParseError("Only single-target assignments are supported.")
        if isinstance(node, ast.Assign) and not isinstance(node.targets[0], ast.Name):
            raise LoopParseError("Only variable assignments are supported.")
        if isinstance(node, ast.AugAssign) and not isinstance(node.target, ast.Name):
            raise LoopParseError("Only variable augmented assignments are supported.")


def _collect_names(tree: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _assigned_names(statements: Iterable[ast.stmt]) -> set[str]:
    assigned: set[str] = set()
    for stmt in statements:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            assigned.add(stmt.targets[0].id)
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            assigned.add(stmt.target.id)
        if isinstance(stmt, ast.If):
            assigned.update(_assigned_names(stmt.body))
            assigned.update(_assigned_names(stmt.orelse))
    return assigned


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node)
