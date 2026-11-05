"""AST instrumentation of the audited SV-COMP subset for concrete ILP32 execution."""
from __future__ import annotations

from pycparser import c_ast, c_generator

from backend.infrastructure.c_source_inventory import main_function
from backend.infrastructure.svcomp_contracts import ContractError, loops_in_source, prepare_source


def instrument_native_trace(source):
    prepare_source(source)  # Same audited helper/call restrictions as the checker.
    tree, loops = loops_in_source(source)
    if len(loops) > 16:
        raise ContractError("Trace runtime supports at most 16 loops")
    indices = {id(node): str(i) for i, node in enumerate(loops)}
    main = main_function(tree)
    scalar_tags, array_tags, types = {}, {}, {}
    scopes = [{}]
    boolean_context = False
    body_depth = 0

    class Generator(c_generator.CGenerator):
        def lookup(self, name):
            for scope in reversed(scopes):
                if name in scope:
                    return scope[name]
            return None

        def visit_Compound(self, node):
            scopes.append({})
            result = super().visit_Compound(node)
            scopes.pop()
            return result

        def visit_Decl(self, node, no_type=False):
            if isinstance(node.type, c_ast.FuncDecl):
                return super().visit_Decl(node, no_type)
            if node.name.startswith("tr_") or node.name == "__trace_start":
                raise ContractError("Source identifier conflicts with trace instrumentation")
            kind = node.type
            while isinstance(kind, (c_ast.TypeDecl, c_ast.ArrayDecl)):
                kind = kind.type
            if not isinstance(kind, c_ast.IdentifierType):
                raise ContractError("Only direct integer locals/arrays are instrumented")
            ctype = " ".join(kind.names)
            if ctype not in {"int", "unsigned int", "unsigned char"}:
                raise ContractError("Unimplemented concrete scalar type: " + ctype)
            array = isinstance(node.type, c_ast.ArrayDecl)
            tags = array_tags if array else scalar_tags
            tag = tags.setdefault(id(node), len(tags))
            if tag >= (16 if array else 128) or array and ctype != "int":
                raise ContractError("Native trace runtime capacity or array type exceeded")
            metadata = dict(name=node.name, tag=tag, array=array, type=ctype)
            scopes[-1][node.name] = metadata
            types[node.name] = ctype + "[]" if array else ctype
            result = self._generate_decl(node)
            if node.init:
                if array:
                    raise ContractError("Array initializer instrumentation not implemented")
                result += " = (tr_defined[" + str(tag) + "]=1, " + self.visit(node.init) + ")"
            return result

        def _generate_stmt(self, node, add_indent=False):
            result = super()._generate_stmt(node, add_indent)
            if isinstance(node, c_ast.Decl) and not isinstance(node.type, c_ast.FuncDecl):
                metadata = self.lookup(node.name)
                if metadata["array"]:
                    result += self._make_indent() + f"tr_init_array({metadata['tag']}, sizeof({node.name})/sizeof({node.name}[0]));\n"
                elif node.init is None:
                    result += self._make_indent() + f"tr_defined[{metadata['tag']}]=0;\n"
            return result

        def visit_Assignment(self, node):
            match node.lvalue:
                case c_ast.ID(name=name):
                    metadata = self.lookup(name)
                    original = super().visit_Assignment(node)
                    if metadata is None or metadata["array"]:
                        raise ContractError("Unmodelled scalar assignment")
                    return f"(({original}), tr_defined[{metadata['tag']}]=1, {name})"
                case c_ast.ArrayRef(name=c_ast.ID(name=name), subscript=index):
                    metadata = self.lookup(name)
                    if not metadata or not metadata["array"] or node.op != "=":
                        raise ContractError("Unsupported array write")
                    return f"tr_store({metadata['tag']}, {name}, {self.visit(index)}, {self.visit(node.rvalue)})"
                case _:
                    raise ContractError("Indirect writes are not trace-supported")

        def visit_ArrayRef(self, node):
            if not isinstance(node.name, c_ast.ID):
                raise ContractError("Indirect array read")
            metadata = self.lookup(node.name.name)
            if not metadata or not metadata["array"]:
                raise ContractError("Unregistered array read")
            return f"tr_load({metadata['tag']}, {node.name.name}, {self.visit(node.subscript)})"

        def visit_UnaryOp(self, node):
            if node.op in {"p++", "p--", "++", "--"}:
                if not isinstance(node.expr, c_ast.ID) or not self.lookup(node.expr.name):
                    raise ContractError("Unsupported increment/decrement target")
                metadata = self.lookup(node.expr.name)
                return f"(tr_defined[{metadata['tag']}]=1, {super().visit_UnaryOp(node)})"
            if node.op in {"sizeof", "_Alignof", "&", "*"}:
                raise ContractError("Unsupported pointer/unevaluated source expression")
            return super().visit_UnaryOp(node)

        def visit_FuncCall(self, node):
            name = node.name.name
            helpers = {"__VERIFIER_assert": "trace_assert", "__VERIFIER_assume": "trace_assume", "assume_abort_if_not": "trace_assume"}
            nondet = {"__VERIFIER_nondet_int": "tr_nd_int", "__VERIFIER_nondet_uint": "tr_nd_uint", "__VERIFIER_nondet_uchar": "tr_nd_uchar"}
            if name in nondet:
                role = 1 if boolean_context else 2 if body_depth else 0
                return nondet[name] + "(" + str(role) + ")"
            if name in helpers:
                return helpers[name] + "(" + self.visit(node.args) + ")"
            if name in {"reach_error", "abort"}:
                return 'tr_stop("assertion_failed")' if name == "reach_error" else 'tr_stop("program_abort")'
            raise ContractError("Unimplemented call: " + name)

        def condition(self, node):
            nonlocal boolean_context
            previous = boolean_context
            boolean_context = True
            result = self.visit(node) if node else "1"
            boolean_context = previous
            return result

        def head(self, node):
            visible = {}
            for scope in scopes:
                visible.update(scope)
            items = []
            for name, metadata in visible.items():
                tag = metadata["tag"]
                if metadata["array"]:
                    items.append(f'tr_array("{name}", {tag}, {name})')
                else:
                    items.append(f'(tr_defined[{tag}] ? tr_scalar("{name}", (long long){name}) : 0)')
            return "(tr_begin(" + indices[id(node)] + ") ? (" + ", ".join(items + ["tr_end()", "0"]) + ") : 0)"

        def visit_While(self, node):
            nonlocal body_depth
            header = self.head(node)
            condition = self.condition(node.cond)
            body_depth += 1
            body = self._generate_stmt(node.stmt, add_indent=True)
            body_depth -= 1
            return f"while (({header}, {condition}))\n" + body

        def visit_For(self, node):
            nonlocal body_depth
            scopes.append({})
            init = self.visit(node.init) if node.init else ""
            header, condition = self.head(node), self.condition(node.cond)
            update = self.visit(node.next) if node.next else ""
            body_depth += 1
            body = self._generate_stmt(node.stmt, add_indent=True)
            body_depth -= 1
            result = f"for ({init}; ({header}, {condition}); {update})\n" + body
            scopes.pop()
            return result

        def visit_If(self, node):
            result = "if (" + self.condition(node.cond) + ")\n" + self._generate_stmt(node.iftrue, add_indent=True)
            if node.iffalse:
                result += self._make_indent() + "else\n" + self._generate_stmt(node.iffalse, add_indent=True)
            return result

    main.decl.name = "trace_program"
    main.decl.type.type.declname = "trace_program"
    program = Generator().visit(main)
    wrapper = '''
int main(int argc, char **argv) {
    if (argc != 6) return 99;
    tr_setup((unsigned)strtoul(argv[1],0,10),(unsigned)strtoul(argv[2],0,10),
        (unsigned)strtoul(argv[3],0,10),strtoull(argv[4],0,10),(unsigned)strtoul(argv[5],0,10));
    int result = trace_program();
    tr_stop("completed");
    return result;
}
'''
    return program + wrapper, types, len(loops)
