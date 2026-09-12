# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32` variable declarations (const by
default, `mut` for mutable), assignments, and `exit`. Source is tokenized by a
hand-written lexer (byte-level state machine), then compiled to LLVM IR via
llvmlite.

## Run the compiler
    python3 compiler.py input.txt output.ll
    lli output.ll                                          # quick run, no linking
    llc -filetype=obj -relocation-model=pic output.ll -o output.o
    clang -fPIE output.o -o program && ./program

## Language
    i32 x{5}          # const declaration, initialiser mandatory
    i32 mut y{10}      # mutable declaration
    y := x + 3         # assignment (mut only), same rules as an initialiser
    exit y             # exit y or exit 42, must be the last line

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
