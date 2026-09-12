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



def main_cli():
    src_path, out_path = sys.argv[1], sys.argv[2]

    with open(src_path) as f:
        lines = f.readlines()

    module = ir.Module(name="practice1")
    module.triple = llvm.get_default_triple()

    main = ir.Function(module, ir.FunctionType(I32, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))

    printf = ir.Function(module, ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
                          name="printf")

    text = b"Program exit with result %d\n\0"
    fmt = ir.GlobalVariable(module, ir.ArrayType(I8, len(text)), name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(ir.ArrayType(I8, len(text)), bytearray(text))

    symbols = {}
    exited = False

    def error(line_no, msg):
        print(f"compilation error: line {line_no}: {msg}", file=sys.stderr)
        sys.exit(1)

    def resolve(tok, line_no):
        tok = tok.strip()
        if tok.isdigit():
            return ir.Constant(I32, int(tok))
        if tok not in symbols:
            error(line_no, f"undeclared variable '{tok}'")
        return builder.load(symbols[tok])

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        if line.startswith("int "):
            name = line[4:].strip()
            if name in symbols:
                error(line_no, f"variable '{name}' already declared")
            symbols[name] = builder.alloca(I32, name=name)

        elif line.startswith("exit "):
            name = line[5:].strip()
            if name not in symbols:
                error(line_no, f"undeclared variable '{name}'")
            val = builder.load(symbols[name])
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), val])
            builder.ret(ir.Constant(I32, 0))
            exited = True

        elif ":=" in line:
            target, expr = line.split(":=", 1)
            target = target.strip()
            expr = expr.strip()
            if target not in symbols:
                error(line_no, f"undeclared variable '{target}'")

            op = None
            for candidate in ("+", "-", "*"):
                if candidate in expr:
                    op = candidate
                    break

            if op:
                lhs_tok, rhs_tok = expr.split(op, 1)
                lhs = resolve(lhs_tok, line_no)
                rhs = resolve(rhs_tok, line_no)
                if op == "+":
                    result = builder.add(lhs, rhs)
                elif op == "-":
                    result = builder.sub(lhs, rhs)
                else:
                    result = builder.mul(lhs, rhs)
            else:
                result = resolve(expr, line_no)

            builder.store(result, symbols[target])

        else:
            error(line_no, f"cannot parse line: '{line}'")

    if not exited:
        error(line_no, "missing 'exit' statement")

    with open(out_path, "w") as f:
        f.write(str(module))

if __name__ == "__main__":
    main_cli()
