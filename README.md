# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32`/`i64`/`bool` variable
declarations (const by default, `mut` for mutable), assignments, `exit`,
comparisons, `!`, `if`/`else` and `while` with blocks, structs with
field access, and functions with calls. Source is tokenized by a
hand-written lexer, parsed by a hand-written recursive-descent parser
into an AST, checked by a `SemanticChecker` visitor that resolves names,
scopes, fields and types before any LLVM instruction exists, and
compiled to LLVM IR via llvmlite by a walk over that tree. The
initialiser and the right side of `:=` accept a chain of `+`, `-`, `*`
with real precedence and left associativity, optionally followed by one
`==`/`!=` comparison. An `i32` value widens into `i64` where needed
(`sext`); nothing else converts.

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
or `while` is a statement, so it may stand inside another block. `{` and
`}` are plain tokens: the lexer does not pair them, the parser does.
There are no grouping parentheses and no unary minus.

## Structs
    struct Point
    {
        i32 mut x
        i32 mut y
    }
    struct Circle
    {
        Point center
        i32 mut radius
    }
    Point p{10, 20}           # one value per field, in the order of the fields
    Circle c{p, 5}            # a struct field takes an object or a call of that type
    Point q{p}                # one value of the struct's own type copies the object
    Circle d{{1, 2}, 5}       # nested {} for a struct field, same rules, any depth

A field is const unless it is marked `mut`. Its type is a built-in type or
a struct declared above; a struct cannot contain itself, has at least one
field, and no two fields share a name. A `{}` for a built-in field, or a
`{}` with the wrong number of values, is an error at that `{`.

## Field access
    i32 cx{c.center.x}        # read: a chain can stand wherever a factor can
    c.radius := 7             # write: the object and every field on the way are mut

Every link of a chain must exist. A write through `a.b.c` needs `a`, `b`
and `c` to be `mut`. `:=` replaces a whole object only if all its fields,
at every depth, are `mut`.

## Functions
    fn moved := (Point p, i32 dx) -> Point
    {
        Point q{p.x + dx, p.y}
        exit q
    }
    Point r{moved(p, 1)}

A function has a name, any number of parameters and a result type; the
types are built-in types or structs. The body is a block whose last line
is `exit`; inside a function `exit` ends the function and its value must
have the result type (which may be a struct). An `exit` inside an `if`
ends the function early. Parameters are copies and cannot be changed.
The body sees only its parameters, its own variables and the functions
of the program, not the top-level variables. A function may call any
function, also itself or one declared below it: all signatures are
collected first, then the bodies are checked and generated. A call has
the result type of its function; the number and types of the arguments
must match the parameters, and an `i32` argument may go into an `i64`
parameter. A call alone is not a statement. No function or struct may be
declared inside a function body, and a function cannot share a name with
a struct.

## Program order
    program ::= { struct } { fn } { statement } exit

Structs come first, then functions, then statements, then the final
`exit`; nothing follows it. A struct after a function or a statement, or
a function after a statement, is an error at that line. `exit` takes one
operand (a constant, a variable, a field chain, a call or `!` with a
factor), never an operation; at the top level the operand has a built-in
type.

## Type rules
A decimal constant takes the narrowest type it fits (`i32`, else `i64`,
else an error). `+ - *` take two integers and produce the wider type; a
`bool` operand is an error. `==`/`!=` take two integers of any width or
two `bool`s and produce `bool`; mixing `bool` with an integer is an
error. A struct operand of an operator is an error. `!` takes a `bool`.
The condition of `if` and `while` must be `bool`. An `i32` may initialise
or be assigned to an `i64` variable or field, or be passed to an `i64`
parameter; nothing else narrows or crosses the integer/`bool` line.

## Scopes
Every block is a scope of its own. The semantic pass keeps a stack of
frames: a block pushes an empty frame on entry and pops it on exit. A
declaration goes into the top frame, and only the top frame is checked
for a duplicate, so an inner block may declare a name again with any type
(shadowing). A use is looked up from the top frame down and takes the
first hit; a name that is in no frame (including one whose block has
ended) is "used before its declaration". A function body starts from a
fresh stack whose bottom frame holds only its parameters. Every `Var`,
`Member` and `Assign` node gets `node.decl`, the declaration it resolved
to, and a chain also gets the list of field indices, so the code
generator never looks up a name or a field.

## Code generation
Every `if` becomes `then`, `else` (when there is one) and `merge` basic
blocks and one conditional branch on the `i1` the condition produced; a
`while` becomes `while.cond`, `while.body` and `while.end` with a branch
back to the condition. Each block ends with exactly one terminator. A
block that ends in `exit` has its `ret` and gets no extra `br`. Every
`alloca` goes into the entry block of its function, whichever block the
declaration is in, so `opt -passes=mem2reg` can promote the slots to
registers and build the `phi` for a variable assigned in both arms of an
`if`.

A struct is a named type, `%"Point" = type {i32, i32}`; an object is an
`alloca` of that type, and a field is reached with `getelementptr` by the
index the semantic pass found, one `gep` per link of a chain. Objects are
passed to and returned from functions by value. A nested `{}` is one
value of the struct type, built from `undef` field by field with
`insertvalue` and stored with one `store`.

Each function of the program becomes a function of the module named
`fn.<name>`, so a function called `main` or `printf` does not clash with
the runtime. All functions are declared before any body is generated.
Parameters are stored into slots in the function's own entry block;
`exit` inside a function becomes `ret`, a call becomes `call`. The
top-level `exit` prints `Program exit with result ...` and returns 0
from `main`.

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
