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
    "read": "keyword", "print": "keyword",
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

    def checked_label(self):
        return self.label()

    def checked_children(self):
        return self.children()

    def dump(self, indent=0, checked=False):
        label = self.checked_label() if checked else self.label()
        print("  " * indent + label)
        kids = self.checked_children() if checked else self.children()
        for child in kids:
            child.dump(indent + 1, checked)

class FieldNode(Node):
    def __init__(self, line, col, name, type_name, mutable):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable

    def label(self):
        return f"Member {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"


class StructNode(Node):
    def __init__(self, line, col, name, fields, fns):
        super().__init__(line, col)
        self.name = name
        self.fields = fields
        self.fns = fns

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields + self.fns

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

class MemberFnNode(FnNode):
    def label(self):
        return f"MemberFn {self.name} -> {self.ret_type}"

    def accept(self, visitor):
        return visitor.visit_member_fn(self)

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

    def checked_label(self):
        if getattr(self, "sugar", None) is not None:
            return f"CallableCall {self.name}"
        return self.label()

class MemberNode(ExprNode):
    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name = name
        self.fields = fields

    def label(self):
        return "Field " + ".".join([self.name] + [f[0] for f in self.fields])

    def accept(self, visitor):
        return visitor.visit_member(self)

class MemberCallNode(ExprNode):
    def __init__(self, line, col, target, name, args):
        super().__init__(line, col)
        self.target = target
        self.name = name
        self.args = args

    def label(self):
        return f"MemberCall {self.name}"

    def children(self):
        return [self.target] + self.args

    def accept(self, visitor):
        return visitor.visit_member_call(self)

    def checked_label(self):
        sugar = getattr(self, "sugar", None)
        if sugar is not None:
            return f"CallableCall {sugar.shown}"
        return self.label()

    def checked_children(self):
        if getattr(self, "sugar", None) is not None:
            return self.args
        return self.children()

class ResultFieldNode(ExprNode):
    def __init__(self, line, col, target, fields):
        super().__init__(line, col)
        self.target = target
        self.fields = fields

    def label(self):
        return "ResultField " + ".".join(f[0] for f in self.fields)

    def children(self):
        return [self.target]

    def accept(self, visitor):
        return visitor.visit_result_field(self)

class ReadNode(ExprNode):
    def label(self):
        return "Read"

    def accept(self, visitor):
        return visitor.visit_read(self)


class StringNode(ExprNode):
    def __init__(self, line, col, text):
        super().__init__(line, col)
        self.text = text

    def label(self):
        return f'String "{self.text}"'

    def accept(self, visitor):
        return visitor.visit_string(self)


class PrintNode(StmtNode):
    def __init__(self, line, col, arg):
        super().__init__(line, col)
        self.arg = arg

    def label(self):
        return "Print"

    def children(self):
        return [self.arg]

    def accept(self, visitor):
        return visitor.visit_print(self)

class CaptureNode(Node):
    def __init__(self, line, col, name, mutable=False):
        super().__init__(line, col)
        self.name = name
        self.mutable = mutable

    def label(self):
        if self.mutable:
            return f"Capture {self.name} mut"
        return f"Capture {self.name}"


class LambdaNode(StmtNode):
    def __init__(self, line, col, name, captures, params, ret_type, body):
        super().__init__(line, col)
        self.name = name
        self.captures = captures
        self.params = params
        self.ret_type = ret_type
        self.body = body

    def label(self):
        return f"Lambda {self.name} -> {self.ret_type}"

    def children(self):
        return self.captures + self.params + [self.body]

    def accept(self, visitor):
        return visitor.visit_lambda(self)

    def checked_children(self):
        return [self.struct, self.decl]

class ThisNode(Node):
    def __init__(self, line, col, struct_name):
        super().__init__(line, col)
        self.name = "this"
        self.type_name = struct_name
        self.mutable = True

class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]
        self.structs = {}
        self.fns = {}
        self.current_fn = None
        self.methods = {}
        self.current_struct = None
        self.flags_ready = False
        self.pending = []
        self.ir_names = set()
        self.lambda_structs = []
        self.lambda_ctx = []

    def check(self, tree):
        tree.accept(self)

    def find_name(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name], []
        st = self.current_struct
        if st is not None and any(f.name == name for f in st.fields):
            return self.current_fn.this, [(name, node.line, node.col)]
        return None

    def check_not_captured(self, node, name):
        if not self.lambda_ctx:
            return
        ctx = self.lambda_ctx[-1]
        visible = any(name in frame for frame in ctx["scopes"])
        st = ctx["struct"]
        if not visible and st is not None:
            visible = any(f.name == name for f in st.fields)
        if visible:
            raise CompileError(f"line {node.line}:{node.col}: '{name}' is not captured by lambda "
                               f"'{ctx['name']}': add it to the capture list, [{name}]")

    def resolve_name(self, node, name):
        found = self.find_name(node, name)
        if found is not None:
            return found
        self.check_not_captured(node, name)
        if name in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: '{name}' is a type, not a variable")
        raise CompileError(f"line {node.line}:{node.col}: variable '{name}' is used before its declaration")

    def is_lambda_type(self, t):
        return isinstance(t, str) and t.startswith("lambda.")

    def lambda_note(self, expr):
        name = getattr(expr, "name", expr.type)
        return f"'{name}' (a lambda: its type has no name)"

    def walk_fields(self, type_name, fields):
        t = type_name
        links, path = [], []
        for i, (fname, line, col) in enumerate(fields):
            if self.is_lambda_type(t) and not (i == 0 and self.current_struct is not None
                                               and self.current_struct.name == t):
                raise CompileError(f"line {line}:{col}: cannot access '{fname}': "
                                   f"the captures of a lambda are not visible outside its body")
            if t not in self.structs:
                raise CompileError(f"line {line}:{col}: cannot access field '{fname}' of a value of type {t}")
            field = next((f for f in self.structs[t].fields if f.name == fname), None)
            if field is None:
                raise CompileError(f"line {line}:{col}: struct '{t}' has no field '{fname}'")
            links.append(field)
            path.append(field.index)
            t = field.type_name
        return links, path, t

    def resolve_chain(self, node):
        decl, head = self.resolve_name(node, node.name)
        node.chain = head + node.fields
        links, path, t = self.walk_fields(decl.type_name, node.chain)
        return decl, links, path, t

    def visit_program(self, node):
        node.lambda_structs = self.lambda_structs
        for struct in node.structs:
            struct.accept(self)
        for struct in node.structs:
            for fn in struct.fns:
                self.check_signature(fn)
        self.collect_signatures(node.functions)
        for struct in node.structs:
            for fn in struct.fns:
                fn.accept(self)
        for fn in node.functions:
            fn.accept(self)
        self.mark_mutating()
        for call in self.pending:
            self.check_call_mut(call)
        self.flags_ready = True
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
        fn_names = set()
        for fn in node.fns:
            if fn.name in seen:
                raise CompileError(f"line {fn.line}:{fn.col}: member function '{fn.name}' has the name of a field of '{node.name}'")
            if fn.name in fn_names:
                raise CompileError(f"line {fn.line}:{fn.col}: member function '{fn.name}' is already declared in '{node.name}'")
            fn_names.add(fn.name)
        for fn in node.fns:
            ir_name = f"{node.name}_{fn.name}"
            if ir_name in self.ir_names:
                raise CompileError(f"line {fn.line}:{fn.col}: member function '{node.name}.{fn.name}' "
                                   f"would be '{ir_name}' in the IR, and that name is taken")
            self.ir_names.add(ir_name)
        self.structs[node.name] = node
        self.methods[node.name] = {fn.name: fn for fn in node.fns}
        for fn in node.fns:
            fn.owner = node.name
            fn.this = ThisNode(fn.line, fn.col, node.name)
            fn.mutating = False
            fn.this_calls = []

    def check_known_type(self, type_name, at):
        if type_name not in BUILTIN and type_name not in self.structs:
            raise CompileError(f"line {at.line}:{at.col}: unknown type '{type_name}'")

    def check_signature(self, fn):
        seen = set()
        for p in fn.params:
            if p.name in seen:
                raise CompileError(f"line {p.line}:{p.col}: parameter '{p.name}' is already declared in '{fn.name}'")
            seen.add(p.name)
            self.check_known_type(p.type_name, p)
        self.check_known_type(fn.ret_type, fn)

    def collect_signatures(self, functions):
        for fn in functions:
            if fn.name in self.structs:
                raise CompileError(f"line {fn.line}:{fn.col}: function '{fn.name}' has the name of a struct")
            if fn.name in self.fns:
                raise CompileError(f"line {fn.line}:{fn.col}: function '{fn.name}' is already declared")
            self.check_signature(fn)
            self.fns[fn.name] = fn

    def visit_fn(self, node):
        saved_scopes, saved_fn = self.scopes, self.current_fn
        self.scopes = [{p.name: p for p in node.params}]
        self.current_fn = node
        node.body.accept(self)
        self.scopes, self.current_fn = saved_scopes, saved_fn

    def visit_member_fn(self, node):
        saved = (self.scopes, self.current_fn, self.current_struct)
        self.scopes = [{p.name: p for p in node.params}]
        self.current_fn = node
        self.current_struct = self.structs[node.owner]
        node.body.accept(self)
        self.scopes, self.current_fn, self.current_struct = saved

    def fresh_lambda_name(self, name):
        base = f"lambda.{name}"
        candidate, n = base, 0
        while candidate in self.structs:
            n += 1
            candidate = f"{base}.{n}"
        return candidate

    def visit_lambda(self, node):
        seen = set()
        for cap in node.captures:
            if cap.name in seen:
                raise CompileError(f"line {cap.line}:{cap.col}: '{cap.name}' is captured twice")
            seen.add(cap.name)
            if self.find_name(cap, cap.name) is None:
                raise CompileError(f"line {cap.line}:{cap.col}: cannot capture '{cap.name}': "
                                   f"no variable '{cap.name}' is visible here")
        for p in node.params:
            if p.name in seen:
                raise CompileError(f"line {p.line}:{p.col}: parameter '{p.name}' has the name "
                                   f"of a capture of lambda '{node.name}'")
        fields, inits = [], []
        for cap in node.captures:
            value = VarNode(cap.line, cap.col, cap.name)
            t = value.accept(self)
            field = FieldNode(cap.line, cap.col, cap.name, t, cap.mutable)
            field.capture = True
            fields.append(field)
            inits.append(value)
        call = MemberFnNode(node.line, node.col, "call", node.params, node.ret_type, node.body)
        st = StructNode(node.line, node.col, self.fresh_lambda_name(node.name), fields, [call])
        self.visit_struct(st)
        self.lambda_structs.append(st)
        self.check_signature(call)
        self.lambda_ctx.append({"name": node.name, "scopes": self.scopes, "struct": self.current_struct})
        self.visit_member_fn(call)
        self.lambda_ctx.pop()
        self.mark_mutating()
        decl = DeclNode(node.line, node.col, node.name, st.name,
                        any(cap.mutable for cap in node.captures), inits, node)
        self.visit_decl(decl)
        node.decl, node.struct = decl, st

    def visit_callable(self, node):
        target = VarNode(node.line, node.col, node.name)
        t = target.accept(self)
        if t not in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: '{node.name}' is not callable: it is {t}")
        if "call" not in self.methods[t]:
            raise CompileError(f"line {node.line}:{node.col}: '{node.name}' is not callable: "
                               f"{t} has no member function 'call'")
        sugar = MemberCallNode(node.line, node.col, target, "call", node.args)
        sugar.shown = node.name
        node.sugar = sugar
        node.type = sugar.accept(self)
        return node.type

    def visit_call(self, node):
        st = self.current_struct
        if st is not None and self.is_lambda_type(st.name):
            st = None
        if self.find_name(node, node.name) is not None:
            if (st is not None and node.name in self.methods[st.name]) or node.name in self.fns:
                raise CompileError(f"line {node.line}:{node.col}: '{node.name}' is both a variable "
                                   f"and a function: rename one of them")
            return self.visit_callable(node)
        if st is not None and node.name in self.methods[st.name]:
            fn = self.methods[st.name][node.name]
            node.this = self.current_fn.this
        else:
            fn = self.fns.get(node.name)
            node.this = None
        if fn is None:
            if self.lambda_ctx and node.name == self.lambda_ctx[-1]["name"]:
                raise CompileError(f"line {node.line}:{node.col}: lambda '{node.name}' cannot call itself: "
                                   f"its name is declared after its body")
            self.check_not_captured(node, node.name)
            raise CompileError(f"line {node.line}:{node.col}: function '{node.name}' is not declared")
        if len(node.args) != len(fn.params):
            raise CompileError(f"line {node.line}:{node.col}: '{node.name}' takes "
                               f"{len(fn.params)} argument(s), got {len(node.args)}")
        for i, (arg, param) in enumerate(zip(node.args, fn.params), 1):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg, f"pass argument {i} of '{node.name}'")
        if node.this is not None:
            self.current_fn.this_calls.append(fn)
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
        if self.is_lambda_type(target):
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': "
                               f"it is a lambda, its type has no name")
        if not decl.mutable:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        for (fname, line, col), field in zip(node.chain, links):
            if not field.mutable:
                if getattr(field, "capture", False):
                    raise CompileError(f"line {line}:{col}: cannot assign to '{fname}': the capture "
                                       f"'{fname}' is const, write [mut {fname}]")
                raise CompileError(f"line {line}:{col}: cannot assign to field '{fname}': it is not mut")
        if isinstance(decl, ThisNode):
            self.current_fn.mutating = True
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
        if self.is_lambda_type(have):
            raise CompileError(f"line {node.value.line}:{node.value.col}: "
                               f"cannot exit with {self.lambda_note(node.value)}")
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
        decl, head = self.resolve_name(node, node.name)
        node.decl = decl
        node.chain = head
        node.links, node.path, node.type = self.walk_fields(decl.type_name, head)
        return node.type

    def visit_member(self, node):
        node.decl, node.links, node.path, node.type = self.resolve_chain(node)
        return node.type

    def visit_result_field(self, node):
        t = node.target.accept(self)
        if t not in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: cannot access a field of a value of type {t}")
        node.links, node.path, node.type = self.walk_fields(t, node.fields)
        return node.type

    def call_label(self, node):
        return getattr(node, "shown", None) or node.name

    def visit_field_call(self, node, t):
        field = next(f for f in self.structs[t].fields if f.name == node.name)
        target = node.target
        entry = (node.name, node.line, node.col)
        if isinstance(target, VarNode):
            inner = MemberNode(target.line, target.col, target.name, [entry])
            shown = f"{target.name}.{node.name}"
        elif isinstance(target, MemberNode):
            inner = MemberNode(target.line, target.col, target.name, target.fields + [entry])
            shown = ".".join([target.name] + [f[0] for f in target.fields] + [node.name])
        else:
            inner = ResultFieldNode(node.line, node.col, target, [entry])
            shown = node.name
        if field.type_name not in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: '{shown}' is not callable: "
                               f"it is {field.type_name}")
        if "call" not in self.methods[field.type_name]:
            raise CompileError(f"line {node.line}:{node.col}: '{shown}' is not callable: "
                               f"{field.type_name} has no member function 'call'")
        sugar = MemberCallNode(node.line, node.col, inner, "call", node.args)
        sugar.shown = shown
        node.sugar = sugar
        node.type = sugar.accept(self)
        return node.type

    def visit_member_call(self, node):
        t = node.target.accept(self)
        if t not in self.structs:
            line, col = getattr(node, "dot", (node.line, node.col))
            raise CompileError(f"line {line}:{col}: cannot call '{node.name}' on a value of type {t}: "
                               f"it has no member functions")
        fn = self.methods[t].get(node.name)
        if fn is None and any(f.name == node.name for f in self.structs[t].fields):
            return self.visit_field_call(node, t)
        if fn is None:
            raise CompileError(f"line {node.line}:{node.col}: struct '{t}' has no member function '{node.name}'")
        label = self.call_label(node)
        if len(node.args) != len(fn.params):
            raise CompileError(f"line {node.line}:{node.col}: '{label}' takes "
                               f"{len(fn.params)} argument(s), got {len(node.args)}")
        for i, (arg, param) in enumerate(zip(node.args, fn.params), 1):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg, f"pass argument {i} of '{label}'")
        node.fn = fn
        node.type = fn.ret_type
        node.temp = isinstance(node.target, (CallNode, MemberCallNode, ResultFieldNode))
        if node.temp:
            return node.type
        decl = node.target.decl
        if isinstance(decl, ThisNode):
            self.current_fn.this_calls.append(fn)
        if self.flags_ready:
            self.check_call_mut(node)
        else:
            self.pending.append(node)
        return node.type

    def mark_mutating(self):
        changed = True
        while changed:
            changed = False
            for st in self.structs.values():
                for fn in st.fns:
                    if not fn.mutating and any(c.mutating for c in fn.this_calls):
                        fn.mutating = True
                        changed = True

    def check_call_mut(self, node):
        if not node.fn.mutating:
            return
        target = node.target
        decl = target.decl
        names = [f[0] for f in target.chain]
        if not isinstance(decl, ThisNode):
            names = [decl.name] + names
        desc = ".".join(names)
        shown = getattr(node, "shown", None)
        if not decl.mutable:
            if shown:
                raise CompileError(f"line {node.line}:{node.col}: cannot call '{shown}': "
                                   f"its '{node.name}' changes it, and it is not mut")
            raise CompileError(f"line {node.line}:{node.col}: cannot call '{node.name}' on '{desc}': it is not mut")
        for (fname, _, _), link in zip(target.chain, target.links):
            if not link.mutable:
                if shown:
                    raise CompileError(f"line {node.line}:{node.col}: cannot call '{shown}': "
                                       f"its '{node.name}' changes it, and '{fname}' is not mut")
                raise CompileError(f"line {node.line}:{node.col}: cannot call '{node.name}' on '{desc}': '{fname}' is not mut")

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

    def visit_read(self, node):
        node.type = "i32"
        return node.type

    def visit_string(self, node):
        return None

    def visit_print(self, node):
        if not isinstance(node.arg, StringNode):
            node.arg.accept(self)
            if self.is_lambda_type(node.arg.type):
                raise CompileError(f"line {node.arg.line}:{node.arg.col}: "
                                   f"cannot print {self.lambda_note(node.arg)}")

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if self.is_lambda_type(have):
            raise CompileError(f"line {expr.line}:{expr.col}: cannot {what} with {self.lambda_note(expr)}")
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
        self.member_fns = {}
        self.strings = {}
        self.struct_nodes = {}

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

    def str_ptr(self, text):
        gv = self.strings.get(text)
        if gv is None:
            data = text.encode("latin-1") + b"\0"
            ty = ir.ArrayType(I8, len(data))
            gv = ir.GlobalVariable(self.module, ty, name=f"str.{len(self.strings)}")
            gv.linkage, gv.global_constant = "private", True
            gv.initializer = ir.Constant(ty, bytearray(data))
            self.strings[text] = gv
        return self.builder.bitcast(gv, ir.PointerType(I8))

    def scanf_fn(self):
        try:
            return self.module.get_global("scanf")
        except KeyError:
            ty = ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True)
            return ir.Function(self.module, ty, name="scanf")

    def generate(self, tree):
        tree.accept(self)

    def visit_program(self, node):
        all_structs = node.structs + node.lambda_structs
        for struct in all_structs:
            struct.accept(self)
        self.declare_functions(node.functions)
        self.declare_member_fns(all_structs)
        for struct in all_structs:
            for fn in struct.fns:
                fn.accept(self)
        for fn in node.functions:
            fn.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_struct(self, node):
        st = self.module.context.get_identified_type(node.name)
        st.set_body(*[self.ir_type(f.type_name) for f in node.fields])
        self.struct_types[node.name] = st
        self.struct_nodes[node.name] = node

    def declare_functions(self, functions):
        for fn in functions:
            fnty = ir.FunctionType(self.ir_type(fn.ret_type),
                                   [self.ir_type(p.type_name) for p in fn.params])
            self.fns[fn.name] = ir.Function(self.module, fnty, name=f"fn.{fn.name}")

    def declare_member_fns(self, structs):
        for st in structs:
            this_ty = ir.PointerType(self.struct_types[st.name])
            for fn in st.fns:
                fnty = ir.FunctionType(self.ir_type(fn.ret_type),
                                       [this_ty] + [self.ir_type(p.type_name) for p in fn.params])
                self.member_fns[(st.name, fn.name)] = ir.Function(
                    self.module, fnty, name=f"{st.name}_{fn.name}")

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

    def visit_member_fn(self, node):
        saved = (self.builder, self.function, self.n_slots, self.current_fn)
        fn = self.member_fns[(node.owner, node.name)]
        self.function, self.current_fn, self.n_slots = fn, node, 0
        self.builder = ir.IRBuilder(fn.append_basic_block("entry"))
        fn.args[0].name = "this"
        node.this.ptr = fn.args[0]
        for arg, p in zip(fn.args[1:], node.params):
            arg.name = p.name
            p.ptr = self.new_slot(self.ir_type(p.type_name), p.name)
            self.builder.store(arg, p.ptr)
        node.body.accept(self)
        self.builder, self.function, self.n_slots, self.current_fn = saved

    def visit_call(self, node):
        sugar = getattr(node, "sugar", None)
        if sugar is not None:
            return sugar.accept(self)
        args = [coerce(self.builder, a.accept(self), a.type, p.type_name)
                for a, p in zip(node.args, node.fn.params)]
        if node.this is not None:
            fn = self.member_fns[(node.fn.owner, node.fn.name)]
            return self.builder.call(fn, [node.this.ptr] + args, name=f"{node.name}.ret")
        return self.builder.call(self.fns[node.name], args, name=f"{node.name}.ret")

    def visit_lambda(self, node):
        node.decl.accept(self)

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
        ptr = self.address_of(node.decl.ptr, node.path, node.name)
        return self.builder.load(ptr)

    def visit_member(self, node):
        ptr = self.address_of(node.decl.ptr, node.path, node.name)
        return self.builder.load(ptr)

    def visit_result_field(self, node):
        value = node.target.accept(self)
        for index in node.path:
            value = self.builder.extract_value(value, index, name="res.field")
        return value

    def visit_member_call(self, node):
        sugar = getattr(node, "sugar", None)
        if sugar is not None:
            return sugar.accept(self)
        target = node.target
        if node.temp:
            obj = self.struct_ptr(target, "chain.tmp")
        else:
            obj = self.address_of(target.decl.ptr, target.path, target.name)
        args = [coerce(self.builder, a.accept(self), a.type, p.type_name)
                for a, p in zip(node.args, node.fn.params)]
        fn = self.member_fns[(node.fn.owner, node.fn.name)]
        return self.builder.call(fn, [obj] + args, name=f"{node.name}.ret")

    def printf_call(self, fmt, *args):
        printf = self.module.get_global("printf")
        self.builder.call(printf, [self.str_ptr(fmt)] + list(args))

    def visit_print(self, node):
        arg = node.arg
        if isinstance(arg, StringNode):
            self.printf_call("%s\n", self.str_ptr(arg.text))
            return
        t = arg.type
        if t in self.struct_nodes:
            self.print_struct(self.struct_ptr(arg), t, True)
            return
        value = arg.accept(self)
        if t == "bool":
            chosen = self.builder.select(value, self.str_ptr("true"), self.str_ptr("false"))
            self.printf_call("%s\n", chosen)
        elif t == "i64":
            self.printf_call("%lld\n", value)
        elif t == "i32":
            self.printf_call("%d\n", value)

    def struct_ptr(self, arg, label="print.tmp"):
        if isinstance(arg, (VarNode, MemberNode)):
            return self.address_of(arg.decl.ptr, arg.path, arg.name)
        value = arg.accept(self)
        slot = self.new_slot(self.ir_type(arg.type), label)
        self.builder.store(value, slot)
        return slot

    def print_struct(self, ptr, type_name, newline):
        self.printf_call(f"{type_name}{{")
        for i, field in enumerate(self.struct_nodes[type_name].fields):
            label = ("" if i == 0 else ", ") + f"{field.name}: "
            field_ptr = self.address_of(ptr, [field.index], f"{type_name}.{field.name}")
            if field.type_name in self.struct_nodes:
                self.printf_call(label)
                self.print_struct(field_ptr, field.type_name, False)
            elif field.type_name == "bool":
                value = self.builder.load(field_ptr)
                chosen = self.builder.select(value, self.str_ptr("true"), self.str_ptr("false"))
                self.printf_call(label + "%s", chosen)
            elif field.type_name == "i64":
                self.printf_call(label + "%lld", self.builder.load(field_ptr))
            else:
                self.printf_call(label + "%d", self.builder.load(field_ptr))
        self.printf_call("}\n" if newline else "}")

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

    def visit_read(self, node):
        slot = self.new_slot(I32, "read")
        self.builder.store(ir.Constant(I32, 0), slot)
        self.builder.call(self.scanf_fn(), [self.str_ptr("%d"), slot])
        return self.builder.load(slot, name="read.val")

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
            elif b == ord("["):
                tokens.append(Token("lbracket", "[", line, col))
            elif b == ord("]"):
                tokens.append(Token("rbracket", "]", line, col))
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
            elif b == ord('"'):
                state, start, start_line, start_col = "STRING", i, line, col
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

        elif state == "STRING":
            if b is None or b == 10:
                raise CompileError(f"line {start_line}:{start_col}: string literal is not closed")
            elif b == ord('"'):
                tokens.append(Token("string", data[start + 1:i].decode("latin-1"), start_line, start_col))
                state = "START"

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

    def parse_chain(self, name_tok):
        fields = []
        while (tok := self.peek()) is not None and tok.kind == "dot":
            self.eat()
            field_tok = self.expect("ident", "a field name")
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lparen":
                if fields:
                    target = MemberNode(name_tok.line, name_tok.col, name_tok.text, fields)
                else:
                    target = VarNode(name_tok.line, name_tok.col, name_tok.text)
                args = self.parse_args()
                return MemberCallNode(field_tok.line, field_tok.col, target, field_tok.text, args)
            fields.append((field_tok.text, field_tok.line, field_tok.col))
        if fields:
            return MemberNode(name_tok.line, name_tok.col, name_tok.text, fields)
        return VarNode(name_tok.line, name_tok.col, name_tok.text)

    def parse_postfix(self, node):
        fields, first_dot = [], None
        while (tok := self.peek()) is not None and tok.kind == "dot":
            dot = self.eat()
            if first_dot is None:
                first_dot = dot
            name_tok = self.expect("ident", "a field name")
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lparen":
                target = node
                if fields:
                    target = ResultFieldNode(first_dot.line, first_dot.col, node, fields)
                args = self.parse_args()
                node = MemberCallNode(name_tok.line, name_tok.col, target, name_tok.text, args)
                node.dot = (dot.line, dot.col)
                fields, first_dot = [], None
            else:
                fields.append((name_tok.text, name_tok.line, name_tok.col))
        if fields:
            return ResultFieldNode(first_dot.line, first_dot.col, node, fields)
        return node

    def parse_args(self):
        self.eat()
        args = []
        tok = self.peek()
        if tok is not None and tok.kind != "rparen":
            args.append(self.parse_expr())
            while (tok := self.peek()) is not None and tok.kind == "comma":
                self.eat()
                args.append(self.parse_expr())
        self.expect("rparen", "')'")
        return args

    def parse_call(self, name_tok):
        args = self.parse_args()
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

        if tok.kind == "string":
            self.error("a string literal can only be the argument of 'print'")

        if tok.kind == "keyword" and tok.text == "print":
            self.error("'print' is a statement and has no value")

        if tok.kind == "keyword" and tok.text == "read":
            self.eat()
            self.expect("lparen", "'(' after 'read'")
            nxt = self.peek()
            if nxt is not None and nxt.kind != "rparen":
                self.error("'read' takes no arguments")
            self.expect("rparen", "')'")
            return ReadNode(tok.line, tok.col)

        if tok.kind == "ident":
            self.eat()
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lparen":
                return self.parse_postfix(self.parse_call(tok))
            node = self.parse_chain(tok)
            if isinstance(node, MemberCallNode):
                return self.parse_postfix(node)
            return node

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
            if kind == "ident" and tok.kind == "keyword" and tok.text in ("read", "print"):
                self.error(f"'{tok.text}' is a built-in name and cannot be used as {what}")
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

        fields, fns = [], []
        while True:
            if self.next_line() is None:
                self.error("'{' is never closed", at=lbrace)
            first = self.peek()
            if first.kind == "rbrace":
                self.eat()
                self.expect_eol()
                break
            if first.kind == "keyword" and first.text == "fn":
                if not fields:
                    self.error("a member function cannot come before the first field")
                fns.append(self.parse_fn(MemberFnNode))
                continue
            if fns and (first.kind == "ident" or (first.kind == "keyword" and first.text in BUILTIN)):
                self.error("a field cannot come after a member function")
            fields.append(self.parse_field())
            self.expect_eol()

        if not fields:
            self.error("empty struct", at=lbrace)
        return StructNode(struct_tok.line, struct_tok.col, name_tok.text, fields, fns)

    def parse_params(self):
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
        return params

    def parse_fn(self, node_class=FnNode):
        fn_tok = self.eat()
        name_tok = self.expect("ident", "a function name")
        self.expect_op(":=")
        params = self.parse_params()
        self.expect("arrow", "'->'")
        ret_tok = self.parse_type()
        self.expect_eol()
        body = self.parse_block("'fn'")
        if body.exit_node is None:
            self.error("the body of a function must end with 'exit'", at=body)
        return node_class(name_tok.line, name_tok.col, name_tok.text, params, ret_tok.text, body)

    def is_lambda_start(self):
        tok = self.toks[self.pos + 3] if self.pos + 3 < len(self.toks) else None
        return tok is not None and tok.kind == "lbracket"

    def parse_lambda(self):
        self.eat()
        name_tok = self.expect("ident", "a lambda name")
        self.expect_op(":=")
        self.expect("lbracket", "'['")
        captures = []
        tok = self.peek()
        if tok is not None and tok.kind != "rbracket":
            while True:
                mutable = False
                mod = self.peek()
                if mod is not None and mod.kind == "keyword" and mod.text == "mut":
                    self.eat()
                    mutable = True
                cap_tok = self.expect("ident", "a variable name to capture")
                captures.append(CaptureNode(cap_tok.line, cap_tok.col, cap_tok.text, mutable))
                tok = self.peek()
                if tok is not None and tok.kind == "comma":
                    self.eat()
                else:
                    break
        self.expect("rbracket", "']'")
        params = self.parse_params()
        self.expect("arrow", "'->'")
        ret_tok = self.parse_type()
        self.expect_eol()
        body = self.parse_block("'fn'")
        if body.exit_node is None:
            self.error("the body of a lambda must end with 'exit'", at=body)
        return LambdaNode(name_tok.line, name_tok.col, name_tok.text, captures, params, ret_tok.text, body)

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

    def parse_print(self):
        print_tok = self.eat()
        self.expect("lparen", "'(' after 'print'")
        tok = self.peek()
        if tok is not None and tok.kind == "rparen":
            self.error("'print' needs an argument", at=print_tok)
        if tok is not None and tok.kind == "string":
            self.eat()
            arg = StringNode(tok.line, tok.col, tok.text)
        else:
            arg = self.parse_expr()
        nxt = self.peek()
        if nxt is not None and nxt.kind == "comma":
            self.error("'print' takes one argument", at=nxt)
        self.expect("rparen", "')'")
        return PrintNode(print_tok.line, print_tok.col, arg)

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
        if tok.kind == "keyword" and tok.text == "fn" and self.is_lambda_start():
            return self.parse_lambda()
        if tok.kind == "keyword" and tok.text in ("i32", "i64", "bool"):
            return self.parse_decl()
        if tok.kind == "keyword" and tok.text == "if":
            return self.parse_if()
        if tok.kind == "keyword" and tok.text == "while":
            return self.parse_while()
        if tok.kind == "keyword" and tok.text == "print":
            return self.parse_print()
        if tok.kind == "keyword" and tok.text == "else":
            self.error("'else' without an 'if'")
        if tok.kind == "ident":
            nxt = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
            if nxt is not None and (nxt.kind == "ident" or (nxt.kind == "keyword" and nxt.text in ("mut", "read", "print"))):
                return self.parse_decl()
            return self.parse_assign()
        if tok.kind == "string":
            self.error("a string literal can only be the argument of 'print'")
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
                if self.is_lambda_start():
                    stmts.append(self.parse_lambda())
                else:
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
    if args and args[0] in ("--ast", "--tokens", "--ast-checked"):
        mode = args[0]
        args = args[1:]

    if mode is None and len(args) != 2:
        print("usage: compiler.py [--ast | --ast-checked | --tokens] input.txt [output.ll]", file=sys.stderr)
        sys.exit(2)
    if mode is not None and len(args) != 1:
        print("usage: compiler.py [--ast | --ast-checked | --tokens] input.txt [output.ll]", file=sys.stderr)
        sys.exit(2)

    if args[0].startswith("-"):
        print(f"unknown option {args[0]!r}", file=sys.stderr)
        print("usage: compiler.py [--ast | --ast-checked | --tokens] input.txt [output.ll]", file=sys.stderr)
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
        if mode == "--ast-checked":
            tree.dump(checked=True)
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
