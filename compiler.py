import sys
import os
from llvmlite import ir
import llvmlite.binding as llvm

I32, I64, I1, I8 = ir.IntType(32), ir.IntType(64), ir.IntType(1), ir.IntType(8)

LLVM_TYPES = {"i32": I32, "i64": I64, "bool": I1}

def coerce(builder, value, have, want):
    if have == "i32" and want == "i64":
        return builder.sext(value, I64, name="wide")
    return value

KEYWORDS = {
    "i32": "keyword", "i64": "keyword", "bool": "keyword",
    "mut": "keyword", "exit": "keyword",
    "true": "keyword", "false": "keyword",
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


class ProgramNode(Node):
    def __init__(self, line, col, statements, exit_node):
        super().__init__(line, col)
        self.statements = statements
        self.exit_node = exit_node

    def label(self):
        return "Program"

    def children(self):
        return self.statements + [self.exit_node]

    def codegen(self, builder):
        for stmt in self.statements:
            stmt.codegen(builder)
        self.exit_node.codegen(builder)

    def accept(self, visitor):
        return visitor.visit_program(self)

class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, init):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.init = init

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def codegen(self, builder):
        llvm_ty = LLVM_TYPES[self.type_name]
        value = self.init.codegen(builder)
        value = coerce(builder, value, self.init.type, self.type_name)
        self.ptr = builder.alloca(llvm_ty, name=self.name)
        builder.store(value, self.ptr)

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

    def codegen(self, builder):
        value = self.value.codegen(builder)
        value = coerce(builder, value, self.value.type, self.decl.type_name)
        builder.store(value, self.decl.ptr)

    def accept(self, visitor):
        return visitor.visit_assign(self)

class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def codegen(self, builder):
        value = self.value.codegen(builder)
        printf = builder.module.get_global("printf")

        if self.value.type == "bool":
            fmt = builder.module.get_global("fmt_bool")
            true_ptr = builder.bitcast(builder.module.get_global("true_str"), ir.PointerType(I8))
            false_ptr = builder.bitcast(builder.module.get_global("false_str"), ir.PointerType(I8))
            chosen = builder.select(value, true_ptr, false_ptr)
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), chosen])
        else:
            wide = coerce(builder, value, self.value.type, "i64")
            fmt = builder.module.get_global("fmt")
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), wide])

        builder.ret(ir.Constant(I32, 0))

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

    def codegen(self, builder):
        lhs = self.left.codegen(builder)
        rhs = self.right.codegen(builder)

        if self.op in ("+", "-", "*"):
            lhs = coerce(builder, lhs, self.left.type, self.type)
            rhs = coerce(builder, rhs, self.right.type, self.type)
            if self.op == "+":
                return builder.add(lhs, rhs)
            elif self.op == "-":
                return builder.sub(lhs, rhs)
            else:
                return builder.mul(lhs, rhs)
        else:
            common = "i64" if "i64" in (self.left.type, self.right.type) else self.left.type
            lhs = coerce(builder, lhs, self.left.type, common)
            rhs = coerce(builder, rhs, self.right.type, common)
            pred = "==" if self.op == "==" else "!="
            return builder.icmp_signed(pred, lhs, rhs)

    def accept(self, visitor):
        return visitor.visit_binop(self)

class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def codegen(self, builder):
        return builder.load(self.decl.ptr)

    def accept(self, visitor):
        return visitor.visit_var(self)

class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def codegen(self, builder):
        return ir.Constant(LLVM_TYPES[self.type], int(self.value))

    def accept(self, visitor):
        return visitor.visit_const(self)

class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Bool {'true' if self.value else 'false'}"

    def codegen(self, builder):
        return ir.Constant(I1, int(self.value))

    def accept(self, visitor):
        return visitor.visit_bool(self)


class SemanticChecker:
    def __init__(self):
        self.symbols = {}

    def check(self, tree):
        tree.accept(self)

    def visit_program(self, node):
        for stmt in node.statements:
            stmt.accept(self)
        node.exit_node.accept(self)

    def visit_decl(self, node):
        if node.name in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' already declared")
        node.init.accept(self)
        self.check_assignable(node.init, node.type_name, node, f"initialise '{node.name}'")
        self.symbols[node.name] = node

    def visit_assign(self, node):
        if node.name not in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is used before its declaration")
        decl = self.symbols[node.name]
        if not decl.mutable:
            raise CompileError(f"line {node.line}:{node.col}: cannot assign to '{node.name}': it is not mut")
        node.value.accept(self)
        self.check_assignable(node.value, decl.type_name, node, f"assign to '{node.name}'")
        node.decl = decl

    def visit_exit(self, node):
        node.value.accept(self)

    def visit_binop(self, node):
        lt = node.left.accept(self)
        rt = node.right.accept(self)
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
        if node.name not in self.symbols:
            raise CompileError(f"line {node.line}:{node.col}: variable '{node.name}' is used before its declaration")
        node.decl = self.symbols[node.name]
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

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if isinstance(expr, ConstNode) and want in ("i32", "i64"):
            raise CompileError(f"line {expr.line}:{expr.col}: "
                               f"constant {expr.value} does not fit in {want}")
        raise CompileError(f"line {at.line}:{at.col}: cannot {what} of type {want} "
                            f"with a value of type {have}")

def lex(data: bytes):
    lines, tokens = [], []
    state, start, start_line, start_col = "START", 0, 1, 1
    line, col = 1, 1
    brace_open = False
    brace_line = brace_col = None

    i = 0

    while i <= len(data):
        b = data[i] if i < len(data) else None

        if state == "START":
            if b is None:
                if brace_open:
                    raise CompileError(f"line {brace_line}:{brace_col}: '{{' is not closed before the end of the line")
                break
            elif b in (32, 9):
                pass
            elif b == 10:
                if brace_open:
                    raise CompileError(f"line {brace_line}:{brace_col}: '{{' is not closed before the end of the line")
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
                brace_open = True
                brace_line, brace_col = line, col
            elif b == ord("}"):
                tokens.append(Token("rbrace", "}", line, col))
                brace_open = False
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
                raise CompileError(f"line {start_line}:{start_col}: expected '!='")

        i += 1
        col += 1

    if tokens:
        lines.append(tokens)
    return lines


class Parser:
    def __init__(self, lines):
        self.lines, self.toks, self.pos = lines, [], 0

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
        init = self.parse_expr()
        self.expect("rbrace", "'}'")
        return DeclNode(name_tok.line, name_tok.col, name_tok.text, type_name, mutable, init)

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

    def parse_statement(self):
        tok = self.peek()
        if tok.kind == "keyword" and tok.text in ("i32", "i64", "bool"):
            return self.parse_decl()
        if tok.kind == "ident":
            return self.parse_assign()
        self.error(f"cannot start a statement with '{tok.text}'")

    def parse_program(self):
        stmts, exit_node, last_line = [], None, 1
        for line_no, toks in enumerate(self.lines, start=1):
            if not toks:
                continue
            last_line = line_no
            self.toks, self.pos = toks, 0

            if exit_node is not None:
                self.error("no statements are allowed after 'exit'")

            first = self.peek()
            if first.kind == "keyword" and first.text == "exit":
                exit_node = self.parse_exit()
            else:
                stmts.append(self.parse_statement())

            if self.peek() is not None:
                bad = self.peek()
                self.error(f"unexpected '{bad.text}' after the statement")

        if exit_node is None:
            raise CompileError(f"line {last_line}:1: missing 'exit' statement")

        return ProgramNode(1, 1, stmts, exit_node)

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
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    if mode == "--ast":
        tree.dump()
        return

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
        tree.codegen(builder)
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w") as f:
        f.write(str(module))

if __name__ == "__main__":
    main_cli()
