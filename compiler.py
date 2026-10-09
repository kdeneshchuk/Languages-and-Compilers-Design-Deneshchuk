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


class ProgramNode(Node):
    def __init__(self, line, col, structs, statements, exit_node):
        super().__init__(line, col)
        self.structs = structs
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.structs + self.statements + [self.exit_node]

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
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name = name
        self.value = value

    def label(self):
        return f"Assign {self.name}"

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


class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]
        self.structs = {}

    def check(self, tree):
        tree.accept(self)

    def lookup(self, node, name):
        for frame in reversed(self.scopes):
            if name in frame:
                return frame[name]
        raise CompileError(f"line {node.line}:{node.col}: variable '{name}' is used before its declaration")

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
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
        st = self.structs[node.type_name]
        node.struct = st
        for e in node.inits:
            e.accept(self)
        node.copy = len(node.inits) == 1 and node.inits[0].type == node.type_name
        if node.copy:
            return
        if len(node.inits) != len(st.fields):
            raise CompileError(f"line {node.lbrace.line}:{node.lbrace.col}: "
                               f"'{node.type_name}' needs {len(st.fields)} value(s), got {len(node.inits)}")
        for e, field in zip(node.inits, st.fields):
            self.check_assignable(e, field.type_name, e, f"initialise field '{field.name}'")

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
        decl = self.lookup(node, node.name)
        if not decl.mutable:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")
        if decl.type_name in self.structs:
            path = self.const_field(decl.type_name)
            if path is not None:
                raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': field '{path}' is not mut")
        node.decl = decl

    def visit_exit(self, node):
        node.value.accept(self)
        if node.value.type in self.structs:
            raise CompileError(f"line {node.line}:{node.col}: cannot exit with a value of type {node.value.type}")

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

    def generate(self, tree):
        tree.accept(self)

    def visit_program(self, node):
        for struct in node.structs:
            struct.accept(self)
        for stmt in node.statements:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_struct(self, node):
        st = self.module.context.get_identified_type(node.name)
        st.set_body(*[self.ir_type(f.type_name) for f in node.fields])
        self.struct_types[node.name] = st

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
        value = coerce(self.builder, value, node.value.type, node.decl.type_name)
        self.builder.store(value, node.decl.ptr)

    def visit_exit(self, node):
        value = node.value.accept(self)
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
            elif b == ord("+"):
                tokens.append(Token("operator", "+", line, col))
            elif b == ord("-"):
                tokens.append(Token("operator", "-", line, col))
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
        inits = [self.parse_expr()]
        while (tok := self.peek()) is not None and tok.kind == "comma":
            self.eat()
            inits.append(self.parse_expr())
        self.expect("rbrace", "'}'")
        return DeclNode(name_tok.line, name_tok.col, name_tok.text, type_name, mutable, inits, brace)

    def parse_assign(self):
        name_tok = self.eat()
        tok = self.peek()
        if tok is None:
            self.error(f"expected ':=' after '{name_tok.text}', found end of line")
        if not (tok.kind == "operator" and tok.text == ":="):
            self.error(f"expected ':=' after '{name_tok.text}', got '{tok.text}'")
        self.eat()
        value = self.parse_expr()
        return AssignNode(name_tok.line, name_tok.col, name_tok.text, value)

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
        structs, stmts, exit_node, last_line = [], [], None, 1
        while self.next_line() is not None:
            if exit_node is not None:
                self.error("no statements are allowed after 'exit'")

            first = self.peek()
            if first.kind == "keyword" and first.text == "struct":
                if stmts:
                    self.error("struct declarations come before the statements")
                structs.append(self.parse_struct())
            elif first.kind == "keyword" and first.text == "exit":
                exit_node = self.parse_exit()
            else:
                stmts.append(self.parse_statement())

            self.expect_eol()
            last_line = self.toks[0].line

        if exit_node is None:
            raise CompileError(f"line {last_line}:1: missing 'exit' statement")

        return ProgramNode(1, 1, structs, stmts, exit_node)

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
