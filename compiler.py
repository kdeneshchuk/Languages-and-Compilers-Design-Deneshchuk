import sys
from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)

KEYWORDS = {"i32": "keyword", "mut": "keyword", "exit": "keyword"}

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

    def codegen(self, builder, symbols):
        for stmt in self.statements:
            stmt.codegen(builder, symbols)
        self.exit_node.codegen(builder, symbols)

class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, mutable, init):
        super().__init__(line, col)
        self.name = name
        self.mutable = mutable
        self.init = init

    def label(self):
        return f"Decl {self.name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def codegen(self, builder, symbols):
        if self.name in symbols:
            raise CompileError(f"line {self.line}:{self.col}: variable '{self.name}' already declared")
        value = self.init.codegen(builder, symbols)
        ptr = builder.alloca(I32, name=self.name)
        builder.store(value, ptr)
        symbols[self.name] = {"ptr": ptr, "mut": self.mutable}


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name = name
        self.value = value

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]

    def codegen(self, builder, symbols):
        if self.name not in symbols:
            raise CompileError(f"line {self.line}:{self.col}: variable '{self.name}' is used before its declaration")
        if not symbols[self.name]["mut"]:
            raise CompileError(f"line {self.line}:{self.col}: cannot assign to '{self.name}': it is not mut")
        value = self.value.codegen(builder, symbols)
        builder.store(value, symbols[self.name]["ptr"])


class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def codegen(self, builder, symbols):
        value = self.value.codegen(builder, symbols)
        printf = builder.module.get_global("printf")
        fmt = builder.module.get_global("fmt")
        builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), value])
        builder.ret(ir.Constant(I32, 0))

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

    def codegen(self, builder, symbols):
        lhs = self.left.codegen(builder, symbols)
        rhs = self.right.codegen(builder, symbols)
        if self.op == "+":
            return builder.add(lhs, rhs)
        elif self.op == "-":
            return builder.sub(lhs, rhs)
        else:
            return builder.mul(lhs, rhs)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def codegen(self, builder, symbols):
        if self.name not in symbols:
            raise CompileError(f"line {self.line}:{self.col}: variable '{self.name}' is used before its declaration")
        return builder.load(symbols[self.name]["ptr"])


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def codegen(self, builder, symbols):
        return ir.Constant(I32, int(self.value))

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

    def parse_expr(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind == "operator" and tok.text in ("+", "-"):
            self.eat()
            node = BinOpNode(tok.line, tok.col, tok.text, node, self.parse_term())
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
        self.eat()
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
        return DeclNode(name_tok.line, name_tok.col, name_tok.text, mutable, init)

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
        if tok.kind == "keyword" and tok.text == "i32":
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
    ast_mode = bool(args) and args[0] == "--ast"
    if ast_mode:
        args = args[1:]

    src_path = args[0]
    out_path = args[1] if not ast_mode else None

    with open(src_path, "rb") as f:
        data = f.read()

    try:
        token_lines = lex(data)
        tree = Parser(token_lines).parse_program()
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    if ast_mode:
        tree.dump()
        return

    module = ir.Module(name="practice1")
    module.triple = llvm.get_default_triple()

    main = ir.Function(module, ir.FunctionType(I32, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))

    ir.Function(module, ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True), name="printf")

    text = b"Program exit with result %d\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(I8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(I8, len(text)), bytearray(text))

    try:
        tree.codegen(builder, {})
    except CompileError as e:
        print(f"compilation error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w") as f:
        f.write(str(module))

if __name__ == "__main__":
    main_cli()
