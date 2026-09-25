# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32`/`i64`/`bool` variable
declarations (const by default, `mut` for mutable), assignments, `exit`,
and comparisons. Source is tokenized by a hand-written lexer, parsed by a
hand-written recursive-descent parser into an AST, checked by a
`SemanticChecker` visitor that resolves names and types before any LLVM
instruction exists, and compiled to LLVM IR via llvmlite by a walk over
that tree. The initialiser and the right side of `:=` accept a chain of
`+`, `-`, `*` with real precedence and left associativity, optionally
followed by one `==`/`!=` comparison. An `i32` value widens into `i64`
where needed (`sext`); nothing else converts.

## Run the compiler
    python3 compiler.py input.txt output.ll      # compile
    python3 compiler.py --ast input.txt           # print the AST, write nothing
    python3 compiler.py --tokens input.txt         # print the token list, write nothing
    lli output.ll                                  # quick run, no linking
    llc -filetype=obj -relocation-model=pic output.ll -o output.o
    clang -fPIE output.o -o program && ./program

## Language
    i32 x{5}                # const declaration, initialiser mandatory
    i64 mut y{10}            # mutable declaration
    bool b{true}             # bool declaration, true/false only
    y := x + 3                # assignment (mut only), same rules as an initialiser
    i32 z{2 + 3 * 4}          # chains: * binds tighter than + and -
    bool r{x == y}            # one comparison per expression, weaker than + - *
    exit y                    # exit y, exit 42, or exit b; must be the last line

## Type rules
A decimal constant takes the narrowest type it fits (`i32`, else `i64`,
else an error). `+ - *` take two integers and produce the wider type; a
`bool` operand is an error. `==`/`!=` take two integers of any width or
two `bool`s and produce `bool`; mixing `bool` with an integer is an
error. An `i32` may initialise or be assigned to an `i64` variable;
nothing else narrows or crosses the integer/`bool` line.

## Run tests
    python3 run_tests.py

Runs every `tests/*.txt`, `tests/ok/*.txt`, and `tests/err/*.txt` against
its `.expected`: compiles, runs the result through `lli` when compilation
succeeds, and otherwise compares the compiler's stderr. Prints a
PASS/FAIL line per test and a summary; exits non-zero if anything failed.

Valid programs write output.ll and produce no stderr. Invalid programs
print `compilation error: line L:C: ...` to stderr and exit non-zero,
writing no output file.
