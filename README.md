# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32` variable declarations (const by
default, `mut` for mutable), assignments, and `exit`. Source is tokenized by
a hand-written lexer, parsed by a hand-written recursive-descent parser into
an AST, and compiled to LLVM IR via llvmlite by a walk over that tree. The
initialiser and the right side of `:=` accept a chain of `+`, `-`, `*` with
real precedence and left associativity.

## Run the compiler
    python3 compiler.py input.txt output.ll      # compile, as before
    python3 compiler.py --ast input.txt           # print the AST, write nothing
    lli output.ll                                  # quick run, no linking
    llc -filetype=obj -relocation-model=pic output.ll -o output.o
    clang -fPIE output.o -o program && ./program

## Language
    i32 x{5}              # const declaration, initialiser mandatory
    i32 mut y{10}          # mutable declaration
    y := x + 3              # assignment (mut only), same rules as an initialiser
    i32 z{2 + 3 * 4}        # chains: * binds tighter than + and -
    exit y                  # exit y or exit 42, must be the last line

## Run tests
    for f in tests/*.txt; do
        echo "=== $f ==="
        python3 compiler.py "$f" /tmp/out.ll
        diff <(lli /tmp/out.ll 2>&1) "${f%.txt}.expected" \
            || diff <(python3 compiler.py "$f" /tmp/out.ll 2>&1) "${f%.txt}.expected"
    done

Valid programs write output.ll and produce no stderr. Invalid programs print
`compilation error: line L:C: ...` to stderr and exit non-zero, writing no
output file.
