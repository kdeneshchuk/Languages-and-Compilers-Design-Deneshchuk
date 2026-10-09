import sys
import os
from llvmlite import ir
import llvmlite.binding as llvm

I32, I64, I1, I8 = ir.IntType(32), ir.IntType(64), ir.IntType(1), ir.IntType(8)

LLVM_TYPES = {"i32": I32, "i64": I64, "bool": I1}
BUILTIN = ("i32", "i64", "bool")

def coerce(builder, value, have, want):
    if have == "i32" and want == "i64":
        return builder.sext(value, I64, name="wide")
    return value

KEYWORDS = {
    "i32": "keyword", "i64": "keyword", "bool": "keyword",
    "mut": "keyword", "exit": "keyword",
    "true": "keyword", "false": "keyword",
    "if": "keyword", "else": "keyword", "while": "keyword",
    "struct": "keyword",
    "fn": "keyword",
}

def is_alpha(b):
    return b is not None and (65 <= b <= 90 or 97 <= b <= 122 or b == 95)

def is_digit(b):
    return b is not None and 48 <= b <= 57


class Token:
    def __init__(self, kind, text, line, col):
        self.kind = kind
        self.text = text
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.kind!r}, {self.text!r}, {self.line}, {self.col})"

class CompileError(Exception):
    pass

class Node:
    def __init__(self, line, col):
        self.line = line
        self.col = col

    def label(self):
        return self.__class__.__name__

    def children(self):
        return []

    def dump(self, indent=0):
        print("  " * indent + self.label())
        for child in self.children():
            child.dump(indent + 1)

class FieldNode(Node):
    def __init__(self, line, col, name, type_name, mutable):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable

    def label(self):
        return f"Field {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"


class StructNode(Node):
    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name = name
        self.fields = fields

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields

    def accept(self, visitor):
        return visitor.visit_struct(self)

class ParamNode(Node):
    def __init__(self, line, col, name, type_name):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = False

    def label(self):
        return f"Param {self.name} {self.type_name}"


class FnNode(Node):
    def __init__(self, line, col, name, params, ret_type, body):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.ret_type = ret_type
        self.body = body

    def label(self):
        return f"Fn {self.name} {self.ret_type}"

    def children(self):
        return self.params + [self.body]

    def accept(self, visitor):
        return visitor.visit_fn(self)

class ProgramNode(Node):
    def __init__(self, line, col, structs, functions, statements, exit_node):
        super().__init__(line, col)
        self.structs = structs
        self.functions = functions
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.structs + self.functions + self.statements + [self.exit_node]

    def accept(self, visitor):
        return visitor.visit_program(self)

class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, inits, lbrace):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.inits = inits
        self.lbrace = lbrace

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return self.inits

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, fields, value):
        super().__init__(line, col)
        self.name = name
        self.fields = fields
        self.value = value

    def label(self):
        return "Assign " + ".".join([self.name] + [f[0] for f in self.fields])

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


class BlockNode(Node):
    def __init__(self, line, col, statements, exit_node):
        super().__init__(line, col)
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Block"

    def children(self):
        if self.exit_node is None:
            return self.statements
        return self.statements + [self.exit_node]

    def accept(self, visitor):
        return visitor.visit_block(self)


class IfNode(StmtNode):
    def __init__(self, line, col, condition, then_block, else_block):
        super().__init__(line, col)
        self.condition = condition
        self.then_block = then_block
        self.else_block = else_block

    def label(self):
        return "If"

    def children(self):
        if self.else_block is None:
            return [self.condition, self.then_block]
        return [self.condition, self.then_block, self.else_block]

    def accept(self, visitor):
        return visitor.visit_if(self)

class WhileNode(StmtNode):
    def __init__(self, line, col, condition, body):
        super().__init__(line, col)
        self.condition = condition
        self.body = body

    def label(self):
        return "While"

    def children(self):
        return [self.condition, self.body]

    def accept(self, visitor):
        return visitor.visit_while(self)

class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_exit(self)

class ExprNode(Node):
    pass


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]

    def accept(self, visitor):
        return visitor.visit_binop(self)

class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)

class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {int(self.value)}"

    def accept(self, visitor):
        return visitor.visit_const(self)

class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Bool {'true' if self.value else 'false'}"

    def accept(self, visitor):
        return visitor.visit_bool(self)


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def label(self):
        return "Not"

    def children(self):
        return [self.operand]

    def accept(self, visitor):
        return visitor.visit_not(self)

class InitNode(ExprNode):
    def __init__(self, line, col, items):
        super().__init__(line, col)
        self.items = items

    def label(self):
        return "Init"

    def children(self):
        return self.items

    def accept(self, visitor):
        return visitor.visit_init(self)

class CallNode(ExprNode):
    def __init__(self, line, col, name, args):
        super().__init__(line, col)
        self.name = name
        self.args = args

    def label(self):
        return f"Call {self.name}"

    def children(self):
        return self.args

    def accept(self, visitor):
        return visitor.visit_call(self)

class MemberNode(ExprNode):
    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name = name
        self.fields = fields

    def label(self):
        return "Member " + ".".join([self.name] + [f[0] for f in self.fields])

    def accept(self, visitor):
        return visitor.visit_member(self)

class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]
        self.structs = {}
        self.fns = {}
        self.current_fn = None

    def check(self, tree):
        tree.accept(self)

    def lookup(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        raise CompileError(f"line {node.line}:{node.col}: variable '{name}' is used before its declaration")

    def resolve_chain(self, node):
        decl = self.lookup(node, node.name)
        t = decl.type_name
        links, path = [], []
        for fname, line, col in node.fields:
            if t not in self.structs:
                raise CompileError(f"line {line}:{col}: cannot access field '{fname}' of a value of type {t}")
            field = next((f for f in self.structs[t].fields if f.name == fname), None)
            if field is None:
                raise CompileError(f"line {line}:{col}: struct '{t}' has no field '{fname}'")
            links.append(field)
            path.append(field.index)
            t = field.type_name
        return decl, links, path, t

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        self.collect_signatures(node.functions)
        for fn in node.functions:
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_struct(self, node):
        if node.name in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: struct '{node.name}' is already declared")
        seen = set()
        for index, field in enumerate(node.fields):
            if field.name in seen:
                raise CompileError(f"line {field.line}:{field.col}: field '{field.name}' is already declared in '{node.name}'")
            seen.add(field.name)
            if field.type_name == node.name:
                raise CompileError(f"line {field.line}:{field.col}: struct '{node.name}' cannot contain itself")
            if field.type_name not in BUILTIN and field.type_name not in self.structs:
                raise CompileError(f"line {field.line}:{field.col}: unknown type '{field.type_name}'")
            field.index = index
        self.structs[node.name] = node

    def check_known_type(self, type_name, at):
        if type_name not in BUILTIN and type_name not in self.structs:
            raise CompileError(f"line {at.line}:{at.col}: unknown type '{type_name}'")

    def collect_signatures(self, functions):
        for fn in functions:
            if fn.name in self.structs:
                raise CompileError(f"line {fn.line}:{fn.col}: function '{fn.name}' has the name of a struct")
            if fn.name in self.fns:
                raise CompileError(f"line {fn.line}:{fn.col}: function '{fn.name}' is already declared")
            seen = set()
            for p in fn.params:
                if p.name in seen:
                    raise CompileError(f"line {p.line}:{p.col}: parameter '{p.name}' is already declared in '{fn.name}'")
                seen.add(p.name)
                self.check_known_type(p.type_name, p)
            self.check_known_type(fn.ret_type, fn)
            self.fns[fn.name] = fn

    def visit_fn(self, node):
        saved_scopes, saved_fn = self.scopes, self.current_fn
        self.scopes = [{p.name: p for p in node.params}]
        self.current_fn = node
        node.body.accept(self)
        self.scopes, self.current_fn = saved_scopes, saved_fn

    def visit_call(self, node):
        fn = self.fns.get(node.name)
        if fn is None:
            raise CompileError(f"line {node.line}:{node.col}: function '{node.name}' is not declared")
        if len(node.args) != len(fn.params):
            raise CompileError(f"line {node.line}:{node.col}: '{node.name}' takes "
                               f"{len(fn.params)} argument(s), got {len(node.args)}")
        for i, (arg, param) in enumerate(zip(node.args, fn.params), 1):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg, f"pass argument {i} of '{node.name}'")
        node.fn = fn
        node.type = fn.ret_type
        return node.type

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is already declared in this block")
        t = node.type_name
        if t in BUILTIN:
            if len(node.inits) != 1:
                raise CompileError(f"line {node.lbrace.line}:{node.lbrace.col}: '{t}' variable takes 1 value, got {len(node.inits)}")
            init = node.inits[0]
            init.accept(self)
            self.check_assignable(init, t, node, f"initialise '{node.name}'")
        elif t in self.structs:
            self.check_struct_init(node)
        else:
            raise CompileError(f"line {node.line}:{node.col}: unknown type '{t}'")
        self.scopes[-1][node.name] = node

    def check_struct_init(self, node):
        node.struct = self.structs[node.type_name]
        node.copy = self.check_values(node.inits, node.type_name, node.lbrace)

    def check_values(self, items, type_name, brace):
        st = self.structs[type_name]
        for e in items:
            if not isinstance(e, InitNode):
                e.accept(self)
        if len(items) == 1 and not isinstance(items[0], InitNode) and items[0].type == type_name:
            return True
        if len(items) != len(st.fields):
            raise CompileError(f"line {brace.line}:{brace.col}: "
                               f"'{type_name}' needs {len(st.fields)} value(s), got {len(items)}")
        for e, field in zip(items, st.fields):
            if isinstance(e, InitNode):
                if field.type_name not in self.structs:
                    raise CompileError(f"line {e.line}:{e.col}: field '{field.name}' of type "
                                       f"{field.type_name} cannot take {{}}")
                e.type = field.type_name
                e.struct = self.structs[field.type_name]
                e.copy = self.check_values(e.items, field.type_name, e)
            else:
                self.check_assignable(e, field.type_name, e, f"initialise field '{field.name}'")
        return False

    def const_field(self, struct_name):
        for field in self.structs[struct_name].fields:
            if not field.mutable:
                return field.name
            if field.type_name in self.structs:
                inner = self.const_field(field.type_name)
                if inner is not None:
                    return f"{field.name}.{inner}"
        return None

    def visit_assign(self, node):
        decl, links, path, target = self.resolve_chain(node)
        if not decl.mutable:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        for (fname, line, col), field in zip(node.fields, links):
            if not field.mutable:
                raise CompileError(f"line {line}:{col}: cannot assign to field '{fname}': it is not mut")
        node.value.accept(self)
        full = ".".join([node.name] + [f[0] for f in node.fields])
        self.check_assignable(node.value, target, node, f"assign to '{full}'")
        if target in self.structs:
            const_path = self.const_field(target)
            if const_path is not None:
                raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{full}': field '{const_path}' is not mut")
        node.decl, node.path, node.target = decl, path, target

    def visit_exit(self, node):
        node.value.accept(self)
        have = node.value.type
        if self.current_fn is None:
            if have in self.structs:
                raise CompileError(f"line {node.line}:{node.col}: cannot exit with a value of type {have}")
            return
        fn = self.current_fn
        self.check_assignable(node.value, fn.ret_type, node, f"exit function '{fn.name}'")

    def visit_binop(self, node):
        lt = node.left.accept(self)
        rt = node.right.accept(self)
        for t in (lt, rt):
            if t in self.structs:
                raise CompileError(f"line {node.line}:{node.col}: cannot apply '{node.op}' to {t}")
        if node.op in ("+", "-", "*"):
            if lt == "bool" or rt == "bool":
                raise CompileError(f"line {node.line}:{node.col}: cannot apply '{node.op}' to bool")
            node.type = "i64" if "i64" in (lt, rt) else "i32"
        else:
            if (lt == "bool") != (rt == "bool"):
                other = rt if lt == "bool" else lt
                raise CompileError(f"line {node.line}:{node.col}: cannot compare bool with {other}")
            node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node, node.name)
        node.type = node.decl.type_name
        return node.type

    def visit_member(self, node):
        node.decl, _, node.path, node.type = self.resolve_chain(node)
        return node.type

    def visit_init(self, node):
        raise CompileError(f"line {node.line}:{node.col}: '{{}}' can only initialise a field of a struct type")

    def visit_const(self, node):
        value = int(node.value)
        if value <= 2147483647:
            node.type = "i32"
        elif value <= 9223372036854775807:
            node.type = "i64"
        else:
            raise CompileError(f"line {node.line}:{node.col}: constant {node.value} does not fit in i64")
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type

    def visit_block(self, node):
        self.scopes.append({})
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit_node is not None:
            node.exit_node.accept(self)
        self.scopes.pop()

    def visit_if(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            raise CompileError(
                f"line {node.line}:{node.col}: the condition of 'if' must be bool, got {cond_type}")
        node.then_block.accept(self)
        if node.else_block is not None:
            node.else_block.accept(self)

    def visit_while(self, node):
        cond_type = node.condition.accept(self)
        if cond_type != "bool":
            raise CompileError(
                f"line {node.line}:{node.col}: the condition of 'while' must be bool, got {cond_type}")
        node.body.accept(self)

    def visit_not(self, node):
        operand_type = node.operand.accept(self)
        if operand_type != "bool":
            raise CompileError(
                f"line {node.line}:{node.col}: cannot apply '!' to {operand_type}")
        node.type = "bool"
        return node.type

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if isinstance(expr, ConstNode) and want in ("i32", "i64"):
            raise CompileError(f"line {expr.line}:{expr.col}: "
                               f"constant {expr.value} does not fit in {want}")
        raise CompileError(f"line {at.line}:{at.col}: cannot {what} of type {want} "
                            f"with a value of type {have}")

class CodeGen:
    def __init__(self, builder):
        self.builder = builder
        self.module = builder.module
        self.function = builder.function
        self.n_slots = 0
        self.struct_types = {}
        self.fns = {}
        self.current_fn = None

    def ir_type(self, name):
        if name in LLVM_TYPES:
            return LLVM_TYPES[name]
        return self.struct_types[name]

    def new_slot(self, llvm_ty, name):
        entry = self.function.entry_basic_block
        saved = self.builder.block
        if self.n_slots < len(entry.instructions):
            self.builder.position_before(entry.instructions[self.n_slots])
        else:
            self.builder.position_at_end(entry)
        ptr = self.builder.alloca(llvm_ty, name=name)
        self.n_slots += 1
        self.builder.position_at_end(saved)
        return ptr

    def address_of(self, ptr, path, label):
        for index in path:
            ptr = self.builder.gep(
                ptr,
                [ir.Constant(I32, 0), ir.Constant(I32, index)],
                inbounds=True,
                name=f"{label}.ptr")
        return ptr

    def generate(self, tree):
        tree.accept(self)

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        self.declare_functions(node.functions)
        for fn in node.functions:
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_struct(self, node):
        st = self.module.context.get_identified_type(node.name)
        st.set_body(*[self.ir_type(f.type_name) for f in node.fields])
        self.struct_types[node.name] = st

    def declare_functions(self, functions):
        for fn in functions:
            fnty = ir.FunctionType(self.ir_type(fn.ret_type),
                                   [self.ir_type(p.type_name) for p in fn.params])
            self.fns[fn.name] = ir.Function(self.module, fnty, name=f"fn.{fn.name}")

    def visit_fn(self, node):
        saved = (self.builder, self.function, self.n_slots, self.current_fn)
        fn = self.fns[node.name]
        self.function, self.current_fn, self.n_slots = fn, node, 0
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        for arg, p in zip(fn.args, node.params):
            arg.name = p.name
            p.ptr = self.new_slot(self.ir_type(p.type_name), p.name)
            self.builder.store(arg, p.ptr)
        node.body.accept(self)
        self.builder, self.function, self.n_slots, self.current_fn = saved

    def visit_call(self, node):
        args = [coerce(self.builder, a.accept(self), a.type, p.type_name)
                for a, p in zip(node.args, node.fn.params)]
        return self.builder.call(self.fns[node.name], args, name=f"{node.name}.ret")

    def visit_decl(self, node):
        llvm_ty = self.ir_type(node.type_name)
        if node.type_name in LLVM_TYPES:
            value = node.inits[0].accept(self)
            value = coerce(self.builder, value, node.inits[0].type, node.type_name)
            node.ptr = self.new_slot(llvm_ty, node.name)
            self.builder.store(value, node.ptr)
            return

        values = [e.accept(self) for e in node.inits]
        node.ptr = self.new_slot(llvm_ty, node.name)
        if node.copy:
            self.builder.store(values[0], node.ptr)
            return
        for e, value, field in zip(node.inits, values, node.struct.fields):
            value = coerce(self.builder, value, e.type, field.type_name)
            field_ptr = self.builder.gep(
                node.ptr,
                [ir.Constant(I32, 0), ir.Constant(I32, field.index)],
                inbounds=True,
                name=f"{node.name}.{field.name}.ptr")
            self.builder.store(value, field_ptr)

    def visit_assign(self, node):
        value = node.value.accept(self)
        value = coerce(self.builder, value, node.value.type, node.target)
        ptr = self.address_of(node.decl.ptr, node.path, node.name)
        self.builder.store(value, ptr)

    def visit_exit(self, node):
        value = node.value.accept(self)

        if self.current_fn is not None:
            value = coerce(self.builder, value, node.value.type, self.current_fn.ret_type)
            self.builder.ret(value)
            return

        printf = self.module.get_global("printf")

        if node.value.type == "bool":
            fmt = self.module.get_global("fmt_bool")
            true_ptr = self.builder.bitcast(self.module.get_global("true_str"), ir.PointerType(I8))
            false_ptr = self.builder.bitcast(self.module.get_global("false_str"), ir.PointerType(I8))
            chosen = self.builder.select(value, true_ptr, false_ptr)
            self.builder.call(printf, [self.builder.bitcast(fmt, ir.PointerType(I8)), chosen])
        else:
            wide = coerce(self.builder, value, node.value.type, "i64")
            fmt = self.module.get_global("fmt")
            self.builder.call(printf, [self.builder.bitcast(fmt, ir.PointerType(I8)), wide])

        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        lhs = node.left.accept(self)
        rhs = node.right.accept(self)

        if node.op in ("+", "-", "*"):
            lhs = coerce(self.builder, lhs, node.left.type, node.type)
            rhs = coerce(self.builder, rhs, node.right.type, node.type)
            if node.op == "+":
                return self.builder.add(lhs, rhs)
            elif node.op == "-":
                return self.builder.sub(lhs, rhs)
            else:
                return self.builder.mul(lhs, rhs)
        else:
            common = "i64" if "i64" in (node.left.type, node.right.type) else node.left.type
            lhs = coerce(self.builder, lhs, node.left.type, common)
            rhs = coerce(self.builder, rhs, node.right.type, common)
            pred = "==" if node.op == "==" else "!="
            return self.builder.icmp_signed(pred, lhs, rhs)

    def visit_var(self, node):
        return self.builder.load(node.decl.ptr)

    def visit_member(self, node):
        ptr = self.address_of(node.decl.ptr, node.path, node.name)
        return self.builder.load(ptr)

    def visit_init(self, node):
        values = [e.accept(self) for e in node.items]
        if node.copy:
            return values[0]
        result = ir.Constant(self.ir_type(node.type), ir.Undefined)
        for e, value, field in zip(node.items, values, node.struct.fields):
            value = coerce(self.builder, value, e.type, field.type_name)
            result = self.builder.insert_value(result, value, field.index,
                                               name=f"{node.type}.{field.name}")
        return result

    def visit_const(self, node):
        return ir.Constant(LLVM_TYPES[node.type], int(node.value))

    def visit_bool(self, node):
        return ir.Constant(I1, int(node.value))

    def visit_not(self, node):
        return self.builder.not_(node.operand.accept(self))

    def visit_block(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        if node.exit_node is not None:
            node.exit_node.accept(self)

    def visit_while(self, node):
        cond_bb = self.function.append_basic_block("while.cond")
        body_bb = self.function.append_basic_block("while.body")
        end_bb = self.function.append_basic_block("while.end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        cond = node.condition.accept(self)
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)


    def visit_if(self, node):
        cond = node.condition.accept(self)
        then_bb = self.function.append_basic_block("then")
        else_bb = self.function.append_basic_block("else") if node.else_block else None
        merge_bb = self.function.append_basic_block("merge")
        self.builder.cbranch(cond, then_bb, else_bb or merge_bb)

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        if else_bb is not None:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)


def lex(data: bytes):
    lines, tokens = [], []
    state, start, start_line, start_col = "START", 0, 1, 1
    line, col = 1, 1

    i = 0

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None:
                break
            elif b in (32, 9, 13):
                pass
            elif b == 10:
                lines.append(tokens)
                tokens = []
                line += 1
                col = 0
            elif is_alpha(b):
                state, start, start_line, start_col = "IDENT", i, line, col
            elif is_digit(b):
                state, start, start_line, start_col = "NUMBER", i, line, col
            elif b == ord("{"):
                tokens.append(Token("lbrace", "{", line, col))
            elif b == ord("}"):
                tokens.append(Token("rbrace", "}", line, col))
            elif b == ord(","):
                tokens.append(Token("comma", ",", line, col))
            elif b == ord("."):
                tokens.append(Token("dot", ".", line, col))
            elif b == ord("("):
                tokens.append(Token("lparen", "(", line, col))
            elif b == ord(")"):
                tokens.append(Token("rparen", ")", line, col))
            elif b == ord("+"):
                tokens.append(Token("operator", "+", line, col))
            elif b == ord("-"):
                state, start_line, start_col = "MINUS", line, col
            elif b == ord("*"):
                tokens.append(Token("operator", "*", line, col))
            elif b == ord(":"):
                state, start_line, start_col = "COLON", line, col
            elif b == ord("="):
                state, start_line, start_col = "EQ", line, col
            elif b == ord("!"):
                state, start_line, start_col = "BANG", line, col
            else:
                raise CompileError(f"line {line}:{col}: unexpected byte {chr(b)!r}")

        elif state == "IDENT":
            if b is not None and (is_alpha(b) or is_digit(b)):
                pass
            else:
                word = data[start:i].decode()
                kind = KEYWORDS.get(word, "ident")
                tokens.append(Token(kind, word, start_line, start_col))
                state = "START"
                continue

        elif state == "NUMBER":
            if b is not None and is_digit(b):
                pass
            elif b is not None and is_alpha(b):
                raise CompileError(f"line {line}:{col}: unexpected letter in number")
            else:
                word = data[start:i].decode()
                tokens.append(Token("number", word, start_line, start_col))
                state = "START"
                continue

        elif state == "COLON":
            if b == ord("="):
                tokens.append(Token("operator", ":=", start_line, start_col))
                state = "START"
            else:
                raise CompileError(f"line {start_line}:{start_col}: ':' not followed by '='")

        elif state == "EQ":
            if b == ord("="):
                tokens.append(Token("operator", "==", start_line, start_col))
                state = "START"
            else:
                raise CompileError(f"line {start_line}:{start_col}: expected '=='")

        elif state == "BANG":
            if b == ord("="):
                tokens.append(Token("operator", "!=", start_line, start_col))
                state = "START"
            else:
                tokens.append(Token("operator", "!", start_line, start_col))
                state = "START"
                continue

        elif state == "MINUS":
            if b == ord(">"):
                tokens.append(Token("arrow", "->", start_line, start_col))
                state = "START"
            else:
                tokens.append(Token("operator", "-", start_line, start_col))
                state = "START"
                continue

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines


class Parser:
    def __init__(self, lines):
        self.lines, self.toks, self.pos = lines, [], 0
        self.line_idx = 0

    def peek_line(self):
        while self.line_idx < len(self.lines) and not self.lines[self.line_idx]:
            self.line_idx += 1
        if self.line_idx < len(self.lines):
            return self.lines[self.line_idx]
        return None

    def next_line(self):
        toks = self.peek_line()
        if toks is None:
            return None
        self.line_idx += 1
        self.toks, self.pos = toks, 0
        return toks

    def expect_eol(self):
        tok = self.peek()
        if tok is not None:
            self.error(f"unexpected '{tok.text}' after the statement")

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def parse_fields(self):
        fields = []
        while (tok := self.peek()) is not None and tok.kind == "dot":
            self.eat()
            field_tok = self.expect("ident", "a field name")
            fields.append((field_tok.text, field_tok.line, field_tok.col))
        return fields

    def parse_call(self, name_tok):
        self.eat()
        args = []
        tok = self.peek()
        if tok is not None and tok.kind != "rparen":
            args.append(self.parse_expr())
            while (tok := self.peek()) is not None and tok.kind == "comma":
                self.eat()
                args.append(self.parse_expr())
        self.expect("rparen", "')'")
        return CallNode(name_tok.line, name_tok.col, name_tok.text, args)

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            self.error("expected a constant or a variable, found end of line")
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, tok.text)

        if tok.kind == "keyword" and tok.text in ("true", "false"):
            self.eat()
            return BoolNode(tok.line, tok.col, tok.text == "true")

        if tok.kind == "ident":
            self.eat()
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lparen":
                return self.parse_call(tok)
            fields = self.parse_fields()
            if fields:
                return MemberNode(tok.line, tok.col, tok.text, fields)
            return VarNode(tok.line, tok.col, tok.text)

        if tok.kind == "operator" and tok.text == "!":
            self.eat()
            return NotNode(tok.line, tok.col, self.parse_factor())
        self.error(f"expected a constant or a variable, got '{tok.text}'")

    def parse_term(self):
        node = self.parse_factor()
        while (tok := self.peek()) is not None and tok.kind == "operator" and tok.text == "*":
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_factor())
        return node

    def parse_arith(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind == "operator" and tok.text in ("+", "-"):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_term())
        return node

    def parse_expr(self):
        node = self.parse_arith()
        tok = self.peek()
        if tok is not None and tok.kind == "operator" and tok.text in ("==", "!="):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_arith())
        return node

    def error(self, msg, at=None):
        tok = at if at is not None else self.peek()
        if tok is not None:
            raise CompileError(f"line {tok.line}:{tok.col}: {msg}")
        last = self.toks[-1]
        raise CompileError(f"line {last.line}:{last.col + len(last.text)}: {msg}")

    def expect(self, kind, what):
        tok = self.peek()
        if tok is None:
            self.error(f"expected {what}, found end of line")
        if tok.kind != kind:
            self.error(f"expected {what}, got '{tok.text}'")
        return self.eat()

    def expect_op(self, text):
        tok = self.peek()
        if tok is None:
            self.error(f"expected '{text}', found end of line")
        if not (tok.kind == "operator" and tok.text == text):
            self.error(f"expected '{text}', got '{tok.text}'")
        return self.eat()

    def parse_type(self):
        tok = self.peek()
        if tok is None:
            self.error("expected a type, found end of line")
        if tok.kind == "ident" or (tok.kind == "keyword" and tok.text in ("i32", "i64", "bool")):
            return self.eat()
        self.error(f"expected a type, got '{tok.text}'")

    def parse_field(self):
        type_tok = self.parse_type()
        mutable = False
        tok = self.peek()
        if tok is not None and tok.kind == "keyword" and tok.text == "mut":
            mutable = True
            self.eat()
        name_tok = self.expect("ident", "a field name")
        return FieldNode(name_tok.line, name_tok.col, name_tok.text, type_tok.text, mutable)

    def parse_struct(self):
        struct_tok = self.eat()
        name_tok = self.expect("ident", "a struct name")
        self.expect_eol()

        toks = self.next_line()
        if toks is None:
            self.error("expected '{' on its own line after 'struct', found end of file")
        if self.peek().kind != "lbrace":
            self.error(f"expected '{{' on its own line after 'struct', got '{self.peek().text}'")
        lbrace = self.eat()
        self.expect_eol()

        fields = []
        while True:
            if self.next_line() is None:
                self.error("'{' is never closed", at=lbrace)
            if self.peek().kind == "rbrace":
                self.eat()
                self.expect_eol()
                break
            fields.append(self.parse_field())
            self.expect_eol()

        if not fields:
            self.error("empty struct", at=lbrace)
        return StructNode(struct_tok.line, struct_tok.col, name_tok.text, fields)

    def parse_fn(self):
        fn_tok = self.eat()
        name_tok = self.expect("ident", "a function name")
        self.expect_op(":=")
        self.expect("lparen", "'('")
        params = []
        tok = self.peek()
        if tok is not None and tok.kind != "rparen":
            while True:
                type_tok = self.parse_type()
                param_tok = self.expect("ident", "a parameter name")
                params.append(ParamNode(param_tok.line, param_tok.col, param_tok.text, type_tok.text))
                tok = self.peek()
                if tok is not None and tok.kind == "comma":
                    self.eat()
                else:
                    break
        self.expect("rparen", "')'")
        self.expect("arrow", "'->'")
        ret_tok = self.parse_type()
        self.expect_eol()
        body = self.parse_block("'fn'")
        if body.exit_node is None:
            self.error("the body of a function must end with 'exit'", at=body)
        return FnNode(name_tok.line, name_tok.col, name_tok.text, params, ret_tok.text, body)

    def parse_init_value(self):
        brace = self.peek()
        if brace is not None and brace.kind == "lbrace":
            self.eat()
            items = [self.parse_init_value()]
            while (tok := self.peek()) is not None and tok.kind == "comma":
                self.eat()
                items.append(self.parse_init_value())
            self.expect("rbrace", "'}'")
            return InitNode(brace.line, brace.col, items)
        return self.parse_expr()

    def parse_decl(self):
        type_tok = self.eat()
        type_name = type_tok.text
        mutable = False
        tok = self.peek()
        if tok is not None and tok.kind == "keyword" and tok.text == "mut":
            mutable = True
            self.eat()
        name_tok = self.expect("ident", "a variable name")
        brace = self.peek()
        if brace is None or brace.kind != "lbrace":
            self.error(f"variable '{name_tok.text}' needs an initialiser in {{}}", at=name_tok)
        self.eat()
        inits = [self.parse_init_value()]
        while (tok := self.peek()) is not None and tok.kind == "comma":
            self.eat()
            inits.append(self.parse_init_value())
        self.expect("rbrace", "'}'")
        return DeclNode(name_tok.line, name_tok.col, name_tok.text, type_name, mutable, inits, brace)

    def parse_assign(self):
        name_tok = self.eat()
        fields = self.parse_fields()
        full = ".".join([name_tok.text] + [f[0] for f in fields])
        tok = self.peek()
        if tok is None:
            self.error(f"expected ':=' after '{full}', found end of line")
        if not (tok.kind == "operator" and tok.text == ":="):
            self.error(f"expected ':=' after '{full}', got '{tok.text}'")
        self.eat()
        value = self.parse_expr()
        return AssignNode(name_tok.line, name_tok.col, name_tok.text, fields, value)

    def parse_exit(self):
        exit_tok = self.eat()
        value = self.parse_factor()
        return ExitNode(exit_tok.line, exit_tok.col, value)

    def parse_block(self, after):
        toks = self.next_line()
        if toks is None:
            self.error(f"expected '{{' on its own line after {after}, found end of file")
        if self.peek().kind != "lbrace":
            self.error(f"expected '{{' on its own line after {after}, got '{self.peek().text}'")
        lbrace = self.eat()
        self.expect_eol()

        statements, exit_node = [], None
        while True:
            if self.next_line() is None:
                self.error("'{' is never closed", at=lbrace)
            first = self.peek()
            if first.kind == "rbrace":
                self.eat()
                self.expect_eol()
                break
            if exit_node is not None:
                self.error("statement after 'exit' in the same block")
            if first.kind == "keyword" and first.text == "exit":
                exit_node = self.parse_exit()
            else:
                statements.append(self.parse_statement())
            self.expect_eol()

        if not statements and exit_node is None:
            self.error("empty block", at=lbrace)
        return BlockNode(lbrace.line, lbrace.col, statements, exit_node)

    def parse_if(self):
        if_tok = self.eat()
        condition = self.parse_expr()
        self.expect_eol()
        then_block = self.parse_block("'if'")

        else_block = None
        nxt = self.peek_line()
        if nxt is not None and nxt[0].kind == "keyword" and nxt[0].text == "else":
            self.next_line()
            self.eat()
            self.expect_eol()
            else_block = self.parse_block("'else'")
        return IfNode(if_tok.line, if_tok.col, condition, then_block, else_block)

    def parse_while(self):
        while_tok = self.eat()
        condition = self.parse_expr()
        self.expect_eol()
        body = self.parse_block("'while'")
        return WhileNode(while_tok.line, while_tok.col, condition, body)

    def parse_statement(self):
        tok = self.peek()
        if tok.kind == "keyword" and tok.text in ("i32", "i64", "bool"):
            return self.parse_decl()
        if tok.kind == "keyword" and tok.text == "if":
            return self.parse_if()
        if tok.kind == "keyword" and tok.text == "while":
            return self.parse_while()
        if tok.kind == "keyword" and tok.text == "else":
            self.error("'else' without an 'if'")
        if tok.kind == "ident":
            nxt = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
            if nxt is not None and (nxt.kind == "ident" or (nxt.kind == "keyword" and nxt.text == "mut")):
                return self.parse_decl()
            return self.parse_assign()
        self.error(f"cannot start a statement with '{tok.text}'")

    def parse_program(self):
        structs, functions, stmts, exit_node, last_line = [], [], [], None, 1
        while self.next_line() is not None:
            if exit_node is not None:
                self.error("no statements are allowed after 'exit'")

            first = self.peek()
            if first.kind == "keyword" and first.text == "struct":
                if stmts:
                    self.error("struct declarations come before the statements")
                if functions:
                    self.error("struct declarations come before the functions")
                structs.append(self.parse_struct())
            elif first.kind == "keyword" and first.text == "fn":
                if stmts:
                    self.error("function declarations come before the statements")
                functions.append(self.parse_fn())
            elif first.kind == "keyword" and first.text == "exit":
                exit_node = self.parse_exit()
            else:
                stmts.append(self.parse_statement())

            self.expect_eol()
            last_line = self.toks[0].line

        if exit_node is None:
            raise CompileError(f"line {last_line}:1: missing 'exit' statement")

        return ProgramNode(1, 1, structs, functions, stmts, exit_node)

def main_cli():
    args = sys.argv[1:]

    mode = None
    if args and args[0] in ("--ast", "--tokens"):
        mode = args[0]
        args = args[1:]

    if mode is None and len(args) != 2:
        print("usage: compiler.py [--ast | --tokens] input.txt [output.ll]", file=sys.stderr)
        sys.exit(2)
    if mode is not None and len(args) != 1:
        print("usage: compiler.py [--ast | --tokens] input.txt [output.ll]", file=sys.stderr)
        sys.exit(2)

    if args[0].startswith("-"):
        print(f"unknown option {args[0]!r}", file=sys.stderr)
        print("usage: compiler.py [--ast | --tokens] input.txt [output.ll]", file=sys.stderr)
        sys.exit(2)

    src_path = args[0]
    out_path = args[1] if mode is None else None

    if out_path is not None and os.path.abspath(out_path) == os.path.abspath(src_path):
        print("refusing to overwrite the input file", file=sys.stderr)
        sys.exit(2)

    try:
        with open(src_path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"cannot read {src_path}: {e.strerror}", file=sys.stderr)
        sys.exit(2)

    try:
        token_lines = lex(data)
        if mode == "--tokens":
            for line_tokens in token_lines:
                for tok in line_tokens:
                    print(f"{tok.text!r} {tok.kind} {tok.line}:{tok.col}")
            return
        tree = Parser(token_lines).parse_program()
        SemanticChecker().check(tree)
        if mode == "--ast":
            tree.dump()
            return
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    module = ir.Module(name="practice1")
    module.triple = llvm.get_default_triple()

    main = ir.Function(module, ir.FunctionType(I32, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))

    ir.Function(module, ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True), name="printf")

    text = b"Program exit with result %lld\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(I8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(I8, len(text)), bytearray(text))

    text_bool = b"Program exit with result %s\n\0"
    fmt_bool = ir.GlobalVariable(module, ir.ArrayType(I8, len(text_bool)), name="fmt_bool")
    fmt_bool.linkage, fmt_bool.global_constant = "private", True
    fmt_bool.initializer = ir.Constant(ir.ArrayType(I8, len(text_bool)), bytearray(text_bool))

    true_text = b"true\0"
    true_str = ir.GlobalVariable(module, ir.ArrayType(I8, len(true_text)), name="true_str")
    true_str.linkage, true_str.global_constant = "private", True
    true_str.initializer = ir.Constant(ir.ArrayType(I8, len(true_text)), bytearray(true_text))

    false_text = b"false\0"
    false_str = ir.GlobalVariable(module, ir.ArrayType(I8, len(false_text)), name="false_str")
    false_str.linkage, false_str.global_constant = "private", True
    false_str.initializer = ir.Constant(ir.ArrayType(I8, len(false_text)), bytearray(false_text))

    try:
        CodeGen(builder).generate(tree)
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w") as f:
        f.write(str(module))

if __name__ == "__main__":
    main_cli()
