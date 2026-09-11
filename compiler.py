import sys
from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)

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
