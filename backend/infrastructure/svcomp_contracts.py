"""Structured contract instrumentation for the pinned SV-COMP cohort only."""
from __future__ import annotations

from pycparser import c_ast, c_generator, c_parser

from backend.infrastructure.c_source_inventory import main_function, parse_c_for_inventory


class ContractError(ValueError):
    pass


def describe_source(source, collection):
    if collection == "SV-COMP":
        _, loops = prepare_source(source)
        tree = parse_c_for_inventory(source)
        return c_generator.CGenerator().visit(main_function(tree)), loops
    tree, loops = loops_in_source(source)
    generator = c_generator.CGenerator()
    return generator.visit(main_function(tree)), [dict(index=str(i), kind=type(node).__name__,
        guard=generator.visit(node.cond) if node.cond else "1") for i, node in enumerate(loops)]


def loops_in_source(source):
    tree = parse_c_for_inventory(source)
    loops = []
    def visit(node):
        if isinstance(node, (c_ast.While, c_ast.For)):
            loops.append(node)
        if isinstance(node, c_ast.DoWhile):
            raise ContractError("Do-while contracts are not implemented.")
        for _, child in node.children():
            visit(child)
    visit(main_function(tree))
    return tree, loops


def validate_expression(text):
    """Only side-effect-free C expressions; never splice unchecked C statements."""
    try:
        tree = c_parser.CParser().parse("void candidate(void){ int __candidate = (" + text + "); }")
    except c_parser.ParseError as exc:
        raise ContractError(str(exc)) from exc
    body = tree.ext[0].body.block_items
    if len(tree.ext) != 1 or len(body) != 1 or not isinstance(body[0], c_ast.Decl):
        raise ContractError("Expected one predicate expression.")
    allowed = (c_ast.BinaryOp, c_ast.UnaryOp, c_ast.ID, c_ast.Constant, c_ast.ArrayRef,
               c_ast.Cast, c_ast.Typename, c_ast.TypeDecl, c_ast.IdentifierType, c_ast.PtrDecl,
               c_ast.TernaryOp)
    def check(node):
        if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID) and node.name.name in {"forall", "exists"}:
            args = node.args.exprs if node.args else []
            if len(args) != 2 or not isinstance(args[0], c_ast.ID):
                raise ContractError("Quantifier syntax is forall(index, predicate) or exists(index, predicate).")
            check(args[1])
            return
        if not isinstance(node, allowed) or isinstance(node, c_ast.UnaryOp) and node.op in {"++", "--", "p++", "p--"}:
            raise ContractError("Candidate predicates must be side-effect-free; calls and assignments are forbidden.")
        for _, child in node.children():
            check(child)
    check(body[0].init)
    class ExpressionGenerator(c_generator.CGenerator):
        def visit_FuncCall(self, node):
            variable, predicate = node.args.exprs
            return "__CPROVER_" + node.name.name + " { int " + variable.name + "; " + self.visit(predicate) + " }"
    return ExpressionGenerator().visit(body[0].init)


def prepare_source(source, contracts=None):
    """Retain the main AST and C types; explicitly model SV-COMP harness helpers.

    This is not a C-to-Python translation. The caller must verify the pinned input
    hash. Only the audited cohort's helper protocol is supported, not arbitrary C.
    """
    tree, loops = loops_in_source(source)
    contracts = {} if contracts is None else contracts
    keys = {str(i) for i in range(len(loops))}
    if set(contracts) - keys:
        raise ContractError("Contract references a nonexistent loop index.")
    predicates = {key: validate_expression(value) for key, value in contracts.items() if value.strip()}
    indices = {id(node): str(i) for i, node in enumerate(loops)}

    class Generator(c_generator.CGenerator):
        def loop(self, node, header):
            predicate = predicates.get(indices[id(node)])
            clause = ""
            if predicate:
                targets = loop_assigns(node)
                if targets:
                    clause += "__CPROVER_assigns(" + ", ".join(targets) + ")\n"
                clause += "__CPROVER_loop_invariant(" + predicate + ")\n"
            return header + "\n" + clause + self._generate_stmt(node.stmt, add_indent=True)

        def visit_While(self, node):
            return self.loop(node, "while (" + self.visit(node.cond) + ")")

        def visit_For(self, node):
            parts = [self.visit(item) if item else "" for item in (node.init, node.cond, node.next)]
            return self.loop(node, "for (" + "; ".join(parts) + ")")

    main = main_function(tree)
    allowed_calls = {"reach_error", "__VERIFIER_assert", "assume_abort_if_not", "abort", "__VERIFIER_assume"}
    nondet_declarations = [node for node in tree.ext if isinstance(node, c_ast.Decl)
                           and isinstance(node.type, c_ast.FuncDecl) and node.name.startswith("__VERIFIER_nondet_")]
    allowed_calls.update(node.name for node in nondet_declarations)
    def check_calls(node):
        if isinstance(node, c_ast.FuncCall) and (not isinstance(node.name, c_ast.ID) or node.name.name not in allowed_calls):
            raise ContractError("Unmodelled call in main: " + c_generator.CGenerator().visit(node.name))
        for _, child in node.children():
            check_calls(child)
    check_calls(main)
    # Definitions outside main must be precisely the audited harness functions.
    if any(isinstance(node, c_ast.FuncDef) and node.decl.name not in allowed_calls | {"main"} for node in tree.ext):
        raise ContractError("Unmodelled helper definition.")
    generator = Generator()
    declarations = "\n".join(generator.visit(node) + ";" for node in nondet_declarations)
    harness = '''
void abort(void) { __CPROVER_assume(0); }
void reach_error(void) { __CPROVER_assert(0, "SV-COMP reach_error"); }
void __VERIFIER_assert(int cond) { if (!cond) reach_error(); }
void assume_abort_if_not(int cond) { __CPROVER_assume(cond); }
void __VERIFIER_assume(int cond) { __CPROVER_assume(cond); }
'''
    return declarations + "\n" + harness + generator.visit(main), [
        dict(index=str(i), kind=type(node).__name__, guard=c_generator.CGenerator().visit(node.cond) if node.cond else "1")
        for i, node in enumerate(loops)]


def loop_assigns(loop):
    """Conservative frames for scalar and direct-array writes in this cohort.

    Array targets cover the whole object, never just the current iteration's cell.
    Locals declared in the body do not escape their per-iteration scope.
    """
    local_names, targets = set(), set()
    def declarations(node):
        if isinstance(node, c_ast.Decl):
            local_names.add(node.name)
        for _, child in node.children():
            declarations(child)
    declarations(loop.stmt)
    def target(node):
        if isinstance(node, c_ast.ID):
            if node.name not in local_names:
                targets.add(node.name)
        elif isinstance(node, c_ast.ArrayRef) and isinstance(node.name, c_ast.ID):
            if node.name.name not in local_names:
                targets.add("__CPROVER_object_whole(" + node.name.name + ")")
        else:
            raise ContractError("Pointer/indirect writes need a separately audited assigns model.")
    def visit(node):
        if isinstance(node, c_ast.Assignment):
            target(node.lvalue)
        if isinstance(node, c_ast.UnaryOp) and node.op in {"++", "--", "p++", "p--"}:
            target(node.expr)
        for _, child in node.children():
            visit(child)
    visit(loop)
    return sorted(targets)
