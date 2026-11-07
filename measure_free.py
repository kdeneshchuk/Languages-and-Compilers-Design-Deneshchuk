import gc
import weakref
import compiler as c


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    tree = c.Parser(c.lex(data)).parse_program()
    c.SemanticChecker().check(tree)
    return tree


def weak_decl(tree, name):
    for s in tree.statements:
        if isinstance(s, c.DeclNode) and s.name == name:
            return weakref.ref(s)


def weak_fn(tree, name):
    for f in tree.functions:
        if f.name == name:
            return weakref.ref(f)


def weak_lambda(tree, fn_name):
    for f in tree.functions:
        if f.name == fn_name:
            for s in f.body.statements:
                if isinstance(s, c.LambdaNode):
                    return weakref.ref(s)


def measure(label, path, pick):
    tree = load(path)
    ref = pick(tree)
    c.remove_unused(tree)
    right_after = ref() is None
    gc.collect()
    after_gc = ref() is None
    print(f"{label}: freed right after the pass: {right_after}; after gc.collect(): {after_gc}")


gc.disable()
measure("variable y", "tests/opt/warmup1.txt", lambda t: weak_decl(t, "y"))
measure("function unused", "tests/opt/fn_chain.txt", lambda t: weak_fn(t, "unused"))
measure("lambda inner", "tests/opt/lambda_in_dead_fn.txt", lambda t: weak_lambda(t, "unused"))
