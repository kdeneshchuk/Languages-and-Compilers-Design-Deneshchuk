# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32`/`i64`/`bool` variable
declarations (const by default, `mut` for mutable), assignments, `exit`,
comparisons, `!`, `if`/`else` and `while` with blocks. Source is
tokenized by a hand-written lexer, parsed by a hand-written
recursive-descent parser into an AST, checked by a `SemanticChecker`
visitor that resolves names, scopes and types before any LLVM
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
    opt -passes=mem2reg -S output.ll               # same IR, allocas promoted to registers (phi)
    llc -filetype=obj -relocation-model=pic output.ll -o output.o
    clang -fPIE output.o -o program && ./program

`--ast` runs the semantic pass first, so a program that does not type-check
is rejected in that mode too.

## Language
    i32 x{5}                # const declaration, initialiser mandatory
    i64 mut y{10}            # mutable declaration
    bool b{true}             # bool declaration, true/false only
    y := x + 3                # assignment (mut only), same rules as an initialiser
    i32 z{2 + 3 * 4}          # chains: * binds tighter than + and -
    bool r{x == y}            # one comparison per expression, weaker than + - *
    bool n{!b}                # ! negates a bool; applies to the factor right after it
    exit y                    # exit y, exit 42, exit b or exit !b

Control flow (a block is `{`, one or more lines, `}`, each on its own line):

    if b                      # the condition is on the line of the if
    {
        y := y + 1
    }
    else                      # optional
    {
        y := y - 1
    }

    while i != 11             # same layout; the body returns to the condition
    {
        sum := sum + i
        i := i + 1
    }

A block holds at least one line and may end with `exit` as its last line;
nothing follows an `exit` inside the same block. Blocks nest, and an `if`
or `while` is a statement, so it may stand inside another block. A program
is a list of statements ending with an `exit`; nothing follows it. `{` and
`}` are plain tokens: the lexer does not pair them, the parser does.

## Type rules
A decimal constant takes the narrowest type it fits (`i32`, else `i64`,
else an error). `+ - *` take two integers and produce the wider type; a
`bool` operand is an error. `==`/`!=` take two integers of any width or
two `bool`s and produce `bool`; mixing `bool` with an integer is an
error. `!` takes a `bool`. The condition of `if` and `while` must be
`bool`. An `i32` may initialise or be assigned to an `i64` variable;
nothing else narrows or crosses the integer/`bool` line.

## Scopes
Every block is a scope of its own. The semantic pass keeps a stack of
frames: a block pushes an empty frame on entry and pops it on exit. A
declaration goes into the top frame, and only the top frame is checked
for a duplicate, so an inner block may declare a name again with any type
(shadowing). A use is looked up from the top frame down and takes the
first hit; a name that is in no frame (including one whose block has
ended) is "used before its declaration". Every `Var` and `Assign` node
gets `node.decl`, the declaration it resolved to, so the code generator
never looks a name up.

## Code generation
Every `if` becomes `then`, `else` (when there is one) and `merge` basic
blocks and one conditional branch on the `i1` the condition produced; a
`while` becomes `while.cond`, `while.body` and `while.end` with a branch
back to the condition. Each block ends with exactly one terminator. A
block that ends in `exit` has its `ret` and gets no extra `br`. Every
`alloca` goes into the entry block, whichever block the declaration is
in, so `opt -passes=mem2reg` can promote the slots to registers and build
the `phi` for a variable assigned in both arms of an `if`.

## Run tests
    python3 run_tests.py

Runs every `tests/*.txt`, `tests/ok/*.txt`, and `tests/err/*.txt` against
its `.expected`: compiles, runs the result through `lli` when compilation
succeeds, and otherwise compares the compiler's stderr. Every `.ast`
snapshot is compared with the output of `--ast` for the program next to
it. Prints a PASS/FAIL line per test and a summary; exits non-zero if
anything failed.

Valid programs write output.ll and produce no stderr. Invalid programs
print `compilation error: line L:C: ...` to stderr and exit non-zero,
writing no output file.