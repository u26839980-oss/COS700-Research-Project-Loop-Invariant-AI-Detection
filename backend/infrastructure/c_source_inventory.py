"""Structured inspection only: this module never executes or translates C."""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json

from pcpp import Preprocessor
from pycparser import c_ast, c_parser


class InventoryError(ValueError):
    pass


class InspectionPreprocessor(Preprocessor):
    def on_error(self, file, line, message):
        raise InventoryError(f"{file}:{line}: {message}")

    def on_include_not_found(self, is_malformed, is_system_include, curdir, includepath):
        raise InventoryError(f"Unresolved include: {includepath}; use the upstream preprocessed input.")


def parse_c_for_inventory(source: str):
    processor = InspectionPreprocessor()
    processor.line_directive = None
    # These affect GNU diagnostics/optimization, not this syntactic inventory.
    # The resulting AST is NOT used to certify C execution semantics.
    processor.define("__attribute__(x)")
    processor.define("__extension__")
    processor.parse(source)
    output = io.StringIO()
    processor.write(output)
    return c_parser.CParser().parse(output.getvalue())


def main_function(tree):
    functions = [node for node in tree.ext if isinstance(node, c_ast.FuncDef) and node.decl.name == "main"]
    if len(functions) != 1:
        raise InventoryError("Expected exactly one main function.")
    return functions[0]


def _canonical(node, *, normalize_constants, names=None):
    names = {} if names is None else names
    if node is None:
        return None
    def variable(value):
        if value not in names:
            names[value] = f"v{len(names)}"
        return names[value]
    attrs = {}
    for key in node.attr_names:
        value = getattr(node, key)
        if key in {"name", "declname"} and isinstance(node, (c_ast.Decl, c_ast.TypeDecl, c_ast.ID)):
            value = variable(value)
        if normalize_constants and isinstance(node, c_ast.Constant) and key == "value":
            value = "CONSTANT"
        attrs[key] = value
    return [type(node).__name__, attrs,
            [[label, _canonical(child, normalize_constants=normalize_constants, names=names)] for label, child in node.children()]]


def inventory_c(source: str) -> dict:
    main = main_function(parse_c_for_inventory(source))
    counts, operators, calls, types = Counter(), Counter(), Counter(), Counter()
    max_depth = 0
    nonlinear = False
    def has_variable(node):
        return isinstance(node, (c_ast.ID, c_ast.ArrayRef)) or any(has_variable(child) for _, child in node.children())
    def visit(node, depth=0):
        nonlocal max_depth, nonlinear
        counts[type(node).__name__] += 1
        if isinstance(node, (c_ast.While, c_ast.For, c_ast.DoWhile)):
            depth += 1
            max_depth = max(max_depth, depth)
        if isinstance(node, c_ast.BinaryOp):
            operators[node.op] += 1
            nonlinear |= node.op == "*" and has_variable(node.left) and has_variable(node.right)
        if isinstance(node, c_ast.FuncCall):
            calls[node.name.name if isinstance(node.name, c_ast.ID) else "indirect_call"] += 1
        if isinstance(node, c_ast.IdentifierType):
            types[" ".join(node.names)] += 1
        for _, child in node.children():
            visit(child, depth)
    visit(main.body)
    def fingerprint(normalize_constants):
        content = json.dumps(_canonical(main.body, normalize_constants=normalize_constants), sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()
    features = []
    if counts["If"]:
        features.append("conditional_branches")
    if counts["ArrayDecl"] or counts["ArrayRef"]:
        features.append("arrays")
    if counts["PtrDecl"] or counts["StructRef"]:
        features.append("pointers_or_structs")
    if max_depth > 1:
        features.append("nested_loops")
    if nonlinear:
        features.append("variable_multiplication")
    if operators["%"]:
        features.append("modular_arithmetic")
    if operators["/"]:
        features.append("C_integer_division")
    if any(name == "unknown" or name.startswith("__VERIFIER_nondet") for name in calls):
        features.append("nondeterminism")
    if any("unsigned" in name for name in types):
        features.append("unsigned_machine_integers")
    if operators["<<"] or operators[">>"] or operators["&"] or operators["|"] or operators["^"]:
        features.append("bitwise_operations")
    return dict(features=features, loop_count=sum(counts[k] for k in ("While", "For", "DoWhile")),
        maximum_loop_depth=max_depth, branch_count=counts["If"], scalar_declarations=counts["Decl"],
        operators=dict(operators), calls=dict(calls), types=dict(types),
        alpha_fingerprint=fingerprint(False), constant_normalized_family=fingerprint(True),
        inventory_semantics="Main-function AST only; GNU attributes ignored for inventory, not verification. No C execution or C-to-Python translation.")
