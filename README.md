# Languages and Compilers Design — Deneshchuk

## Current status
A compiler for a small language with `i32`/`i64`/`bool` variable
declarations (const by default, `mut` for mutable), assignments, `exit`,
comparisons, `!`, `if`/`else` and `while` with blocks, structs with
field access and member functions, functions with calls, callable objects, lambdas with captures, and
the built-ins `read()` and `print`. Source is tokenized by a hand-written
lexer, parsed by a hand-written recursive-descent parser into an AST,
checked by a `SemanticChecker` visitor that resolves names, scopes,
fields, member functions and types before any LLVM instruction exists,
and compiled to LLVM IR via llvmlite by a walk over that tree. The
initialiser and the right side of `:=` accept a chain of `+`, `-`, `*`
with real precedence and left associativity, optionally followed by one
`==`/`!=` comparison. An `i32` value widens into `i64` where needed
(`sext`); nothing else converts.

## Run the compiler
    python3 compiler.py input.txt output.ll      # compile
    python3 compiler.py --ast input.txt           # print the AST, write nothing
    python3 compiler.py --ast-checked input.txt   # print the AST after the semantic pass, write nothing
    python3 compiler.py --tokens input.txt         # print the token list, write nothing
    lli output.ll                                  # quick run, no linking
    lli output.ll < input.in                       # a program that calls read()
    opt -passes=mem2reg -S output.ll               # same IR, allocas promoted to registers (phi)
    llc -filetype=obj -relocation-model=pic output.ll -o output.o
    clang -fPIE output.o -o program && ./program

`--ast` runs the semantic pass first, so a program that does not type-check
is rejected in that mode too. `--ast` prints the tree the parser built;
`--ast-checked` prints it after the semantic pass, where a lambda has become
its struct, its `call` and its variable, and a call of an object is a
`CallableCall`.

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

## Member functions
    struct Point
    {
        i32 mut x
        i32 mut y

        fn d2 := () -> i32
        {
            i32 r{x * x + y * y}
            exit r
        }
        fn shift := (i32 dx) -> i32
        {
            x := x + dx
            exit x
        }
    }
    Point mut p{3, 4}
    i32 d{p.d2()}             # 25
    i32 n{p.shift(2)}         # p.x becomes 5, n is 5
    i32 k{c.center.d2()}      # the object can be a field chain

Member functions come after the fields of a struct, never before the
first field and never between fields. A field and a member function of
one struct cannot share a name, nor can two member functions. A member
function is declared like any other function and is called on an object
with `.`; it stands wherever a factor can, also after `exit`. A call ends
a chain: nothing follows its `)` (except the additional task below), and
a call alone is not a statement. A member function is called on an
object, never on a type: `Point.d2()` is an error.

**Names inside a member function.** A name is looked up among the
parameters and local variables first, then among the fields of the
structure, so a parameter or a local named like a field hides the field.
A call `f(...)` looks among the member functions of the same structure
first, then among the global functions: a member function hides a global
function with the same name, and `d2()` inside `Point` calls `d2` on the
same object. As in any function, the body does not see the top-level
variables. A field without `mut` cannot be assigned, also inside a
member function.

**The mutation rule.** A member function is mutating if it assigns to a
field of its object, or calls a mutating member function on its object or
on one of its fields. A mutating function may be called only through a
chain where every link is `mut`: the object and each field on the way. A
non-mutating one may be called on anything. A local variable named like a
field does not make a function mutating. Member functions may call each
other in a cycle, so the checker marks the ones that assign a field, then
repeats until no new function gets marked, and only then checks the calls.

**A call in the middle of a chain (additional task).** A `.` is allowed
after a call whose result is a struct, to read a field of the result or
to call a member function on it:

    i32 a{p.moved(1).shift(2)}        # shift changes the copy, p.x stays 3
    i32 d{c.grown(1).center.d2()}
    i32 e{origin().moved(7).x}

The result is a temporary copy: it can be read and any member function
can be called on it, also a mutating one, but nothing can be assigned to
it (`p.moved(1).x := 5` is an error). A `.` after a call whose result is
not a struct (`p.d2().x`) is an error at that `.`.

## Callable objects
    struct Scale
    {
        i32 k
        fn call := (i32 v) -> i32
        {
            i32 r{v * k}
            exit r
        }
    }
    struct Shape
    {
        Scale scale
        i32 side
        fn grown := () -> i32
        {
            i32 g{scale(side)}
            exit g
        }
    }
    Scale triple{3}
    Shape s{{2}, 5}
    i32 a{triple(14)}         # 42: the same as triple.call(14)
    i32 b{s.scale(7)}         # 14: scale is a field, so this is s.scale.call(7)
    i32 c{s.grown()}          # 10: inside grown, scale(side) calls the field

A struct that declares a member function named `call` can be called like
a function: `triple(14)` means `triple.call(14)`, and the longer form
stays legal. For a name followed by `(` the semantic pass decides:

1. If the name is a variable visible here (a parameter, a local, a field
   inside a member function, a capture inside a lambda), its type must be
   a struct that declares `call`, and the call is `name.call(args)`.
2. Otherwise the name is a function: a member function of the current
   struct first, then a global function.
3. Otherwise it is an unknown function.

If the name is both a visible variable and a function, a call is an
error at the name (`'twice' is both a variable and a function: rename
one of them`); declaring both is allowed. After a dot the same rule
works: in `obj.name(args)`, if `name` is a field of `obj`, the call is
`obj.name.call(args)`.

The parser cannot tell a variable from a function, so `triple(14)` stays
a `Call` in `--ast`. The semantic pass puts a member call in place of it:
the target, the member function `call` and the same arguments. That
member call is checked by the code that already checks member calls (the
arguments and the mutation rule), and the code generator only emits it,
so the IR of `triple(14)` and of `triple.call(14)` is the same line.
`--ast-checked` shows it as `CallableCall`. The messages name the object,
not `call`: `'p' is not callable: Point has no member function 'call'`,
`'n' is not callable: it is i32`, `'triple' takes 1 argument(s), got 2`.
The mutation rule holds as for any other member function: if `call`
assigns a field, `c()` needs a `mut` object (`cannot call 'c': its 'call'
changes it, and it is not mut`), also when `c` is a field or a parameter.

## Lambdas
    i32 mut k{10}
    fn add_k := [k](i32 y) -> i32
    {
        i32 r{k + y}
        exit r
    }
    k := 100
    print(add_k(5))           # 15: add_k keeps the 10 it took at its declaration
    print(k)                  # 100

A lambda is a function declared where a statement stands. The list in
brackets names the variables it takes with it, its captures. A line that
starts with `fn name := [` is a lambda; `fn` without `[` inside a block
stays an error, and so does a capture list on a member function. A lambda
stands at the top level among the statements and inside any block: a
function, a member function, `if`, `while`, another lambda. Its body ends
with `exit`, as the body of any function.

A capture is a copy taken at the declaration: changing the variable later
does not change the lambda, and a lambda declared inside a loop takes the
current value on every iteration. Inside the body a capture is const
(`cannot assign to 'k': the capture 'k' is const, write [mut k]`). The
body sees its parameters, its locals, its captures and the global
functions, nothing else: not the top-level variables, not the locals and
parameters of the enclosing function, not the fields of the enclosing
struct, unless they are captured. A name that is visible where the lambda
is declared but is not in the capture list is reported as `'k' is not
captured by lambda 'add_k': add it to the capture list, [k]`. A name in
the capture list must be visible there and may be written once, and a
parameter cannot have the name of a capture. A lambda cannot call itself:
its name is declared after its body, and the body does not see its own
member function `call` either. A lambda may capture another lambda.

The semantic pass rewrites a lambda into three things the compiler
already has: a struct with one field per capture, of the type of the
captured variable; its member function `call` with the parameters, the
result type and the body of the lambda; and a variable `add_k` of that
struct, initialised at this point with the captured values, as
`lambda.add_k add_k{k}`. The struct is called `lambda.add_k` in the
compiler (a dot cannot appear in a name a program declares, so it never
clashes; a second lambda called `add_k` gets `lambda.add_k.1`). After
that `add_k(5)` is a callable object. `--ast` stays the parse tree
(`Lambda`, `Capture`); `--ast-checked` prints the tree after the
semantic pass: `Struct lambda.add_k`, `MemberFn call`,
`Decl add_k lambda.add_k` and `CallableCall add_k`.

The type of a lambda has no name, so no parameter, result type, variable
or field can have it. A lambda cannot be passed as an argument, be the
value of `exit`, initialise a variable or a field, be assigned or be
printed (`'times' (a lambda: its type has no name)`). Its captures are
not visible outside its body: `add_k.k` is an error.

**Mutable captures (additional task).**

    i32 n{0}
    fn next := [mut n]() -> i32
    {
        n := n + 1
        exit n
    }
    print(next())             # 1
    print(next())             # 2
    fn twice := [mut next]() -> i32
    {
        i32 a{next()}
        i32 b{next()}
        i32 r{a * 10 + b}
        exit r
    }
    print(twice())            # 34: twice counts on its own copy of next
    print(next())             # 3
    exit n                    # 0: the original n never changes

`[mut n]` gives the lambda its own mutable copy of `n`: the field of the
struct is `mut`. A call that assigns a mut capture is a mutating member
function, so the mutation rule decides where it may be called. The
closure variable is `mut` when one of its captures is `mut`, so a call
like `next()` works at the place of the declaration. A capture of another
lambda is const unless it is written `[mut next]`: with `[next]` the call
inside `twice` is an error (`cannot call 'next': its 'call' changes it,
and 'next' is not mut`). The original variable never changes, and a mut
capture of a const variable is allowed: it is a copy.

## read() and print
    i32 a{read()}             # reads one integer from stdin, the result is i32
    print("text")             # a string literal, printed as written
    print(a + 1)              # i32 / i64: the number
    print(a == 3)             # bool: the word true or false
    print(p)                  # a struct: Point{x: 3, y: 4}
    print(c)                  # Circle{center: Point{x: 3, y: 4}, radius: 2}

`read()` stands wherever a factor can (`i32 a{read()}`, `add(read(), 2)`,
`exit read()`). `print(arg)` is a statement: it prints the argument and a
newline, and stands wherever a statement can (top level, functions,
member functions, `if`, `else`, `while`). It has no value, so
`i32 x{print(1)}` is an error. The argument is an `i32`/`i64` value, a
`bool`, a string literal, or a struct object (a variable, a field chain
or a call that returns a struct); a struct field is printed the same way,
one level down. A string literal is `"`, any bytes except `"` and a
newline, then `"`; there are no escape sequences (`\n` is two characters)
and a literal stands only as the argument of `print`. The names `read`
and `print` are reserved: a variable, function, parameter, field or
member function with that name is an error, and so are `read(5)`,
`print()`, `print(a, b)` and `p.print()`. The output of `print` comes
before `Program exit with result N`.

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
a function after a statement, is an error at that line. A lambda is a
statement, not a function: it stands among the statements and inside any
block, so it never breaks this order. `exit` takes one
operand (a constant, a variable, a field chain, a call, `read()` or `!`
with a factor), never an operation; at the top level the operand has a
built-in type.

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
fresh stack whose bottom frame holds only its parameters; a member
function body also falls back to the fields of its structure after the
frames. Every `Var`, `Member` and `Assign` node gets `node.decl`, the
declaration it resolved to, and a chain also gets the list of field
indices, so the code generator never looks up a name or a field.

A lambda body starts from a fresh stack like a function body, with its
parameters in the bottom frame; its captures are the fields of its struct,
found after the frames as in a member function. The names visible where
the lambda is declared are kept only to tell `'k' is not captured` from
`used before its declaration`.

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
passed to and returned from ordinary functions by value. A nested `{}` is
one value of the struct type, built from `undef` field by field with
`insertvalue` and stored with one `store`.

Each function of the program becomes a function of the module named
`fn.<name>`, so a function called `main` or `printf` does not clash with
the runtime. All functions are declared before any body is generated.
Parameters are stored into slots in the function's own entry block;
`exit` inside a function becomes `ret`, a call becomes `call`. The
top-level `exit` prints `Program exit with result ...` and returns 0
from `main`.

**Member functions.** A member function is a function of the module named
after the structure and the function, `Point_shift`. Its first parameter
is `%"Point"* %this`, a pointer to the object it is called on; the
language has no name for it. A call passes the address of the object: its
`alloca`, or a `gep` into it for `c.center.d2()`. A field inside the body
is a `gep` on `this`, so `x := x + dx` is a gep, a load, an add and a
store. Because the object is passed by address, `shift` changes the
caller's object, not a copy. If the generated name `Struct_method` would
collide with another one (`A_B` + `c` against `A` + `B_c`), the checker
reports an error. For a call in the middle of a chain a field of the
result is an `extractvalue`, and for a member function call on a result
the result is stored into a temporary `alloca` (`chain.tmp`) whose
address is passed as `this`.

**Lambdas.** The struct `lambda.add_k` is a type like any other,
`%"lambda.add_k" = type {i32}`, and its `call` is a member function of the
module, `lambda.add_k_call`, with the object as its first parameter. The
checker keeps the structs it creates in `ProgramNode.lambda_structs`, so
the generator declares them with the other structs before any body is
generated. The declaration of the closure variable is an `alloca` in the
entry block, then a `gep` and a `store` of the current value of the
captured variable per capture, at the place of the declaration (so a
lambda inside a loop copies again on every iteration). The body is
generated once, as a function, not where the lambda stands. A call passes
the address of the closure variable, then the arguments: `call i32
@"lambda.add_k_call"(%"lambda.add_k"* %"add_k", i32 5)`.

**read and print.** `printf` and `scanf` are declared once in the module.
`read()` stores 0 into an `alloca` in the entry block, calls `scanf` with
the global constant `"%d"` and loads the slot, so an empty input gives 0.
`print` uses global constants for the formats and texts: `"%d\n"` for
`i32`, `"%lld\n"` for `i64`, `"%s\n"` with a `select` between the globals
`"true"` and `"false"` for `bool`, and `"%s\n"` for a literal, so a `%`
in it stays a `%`. A struct is printed by walking its field table:
`printf("Point{")`, then for every field a gep, a load and a `printf`
with a format like `", y: %d"` (a struct field goes the same way one
level down), then `printf("}")`. A call result is stored into an
`alloca` first.

## Run tests
    python3 run_tests.py

Runs every `tests/*.txt`, `tests/ok/*.txt`, and `tests/err/*.txt` against
its `.expected`: compiles, runs the result through `lli` when compilation
succeeds, and otherwise compares the compiler's stderr. A program that
reads input keeps it next to itself in `NAME.in`; the script passes this
file as the standard input (an empty input when there is none). The whole
standard output is compared: the lines of `print` and the exit line. Every
`.ast` snapshot is compared with the output of `--ast` for the program
next to it. Prints a PASS/FAIL line per test and a summary; exits non-zero
if anything failed.

Valid programs write output.ll and produce no stderr. Invalid programs
print `compilation error: line L:C: ...` to stderr and exit non-zero,
writing no output file.