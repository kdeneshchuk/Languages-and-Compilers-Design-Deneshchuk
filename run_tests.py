import glob
import subprocess
import sys
import os

FLAGS = ["--remove-unused"] if "--remove-unused" in sys.argv else []
OUT_LL = "/tmp/run_tests_out.ll"


def run_output_test(txt_path, flags=None):
    if flags is None:
        flags = FLAGS
    exp_path = txt_path[:-4] + ".expected"
    try:
        with open(exp_path) as f:
            expected = f.read().strip()
    except FileNotFoundError:
        return None

    result = subprocess.run(
        ["python3", "compiler.py", *flags, txt_path, OUT_LL],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        try:
            with open(txt_path[:-4] + ".in") as f:
                stdin_text = f.read()
        except FileNotFoundError:
            stdin_text = ""
        lli_result = subprocess.run(
            ["lli", OUT_LL],
            capture_output=True, text=True, input=stdin_text
        )
        actual = lli_result.stdout.strip()
    else:
        actual = result.stderr.strip()

    return actual == expected, actual, expected


def run_ast_test(ast_path):
    txt_path = ast_path[:-4] + ".txt"
    with open(ast_path) as f:
        expected = f.read().strip()

    result = subprocess.run(
        ["python3", "compiler.py", "--ast", txt_path],
        capture_output=True, text=True
    )
    actual = result.stdout.strip()

    return actual == expected, actual, expected


def run_astc_test(astc_path):
    txt_path = astc_path[:-5] + ".txt"
    with open(astc_path) as f:
        expected = f.read().strip()

    result = subprocess.run(
        ["python3", "compiler.py", "--remove-unused", "--ast-checked", txt_path],
        capture_output=True, text=True
    )
    actual = result.stdout.strip()

    return actual == expected, actual, expected


def report(path, ok, actual, expected, counters):
    if ok:
        counters["passed"] += 1
        print(f"PASS  {path}")
    else:
        counters["failed"] += 1
        print(f"FAIL  {path}")
        print(f"      expected: {expected!r}")
        print(f"      actual:   {actual!r}")

def build_binary(txt_path, flags, exe_path, c_files=()):
    ll = exe_path + ".ll"
    obj = exe_path + ".o"
    steps = [
        ["python3", "compiler.py", *flags, txt_path, ll],
        ["llc", "-filetype=obj", "-relocation-model=pic", ll, "-o", obj],
        ["clang", "-fPIE", obj, *c_files, "-o", exe_path],
    ]
    for cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return result.stderr.strip() or f"{cmd[0]} failed"
    return None

def run_binary_tests(counters):
    for directory in ("tests/io", "tests/ffi"):
        for txt_path in sorted(glob.glob(f"{directory}/*.txt")):
            name = os.path.basename(txt_path)[:-4]
            exe_path = f"/tmp/bin_{os.path.basename(directory)}_{name}"
            c_path = f"{directory}/{name}.c"
            c_files = [c_path] if os.path.exists(c_path) else []
            error = build_binary(txt_path, FLAGS, exe_path, c_files)
            if error is not None:
                report(txt_path, False, error, "a binary", counters)
                continue
            for in_path in sorted(glob.glob(f"{directory}/{name}.*.in")):
                session = in_path[:-3]
                with open(session + ".expected") as f:
                    expected = f.read()
                with open(in_path) as f:
                    stdin_text = f.read()
                result = subprocess.run([exe_path], capture_output=True,
                                        text=True, input=stdin_text)
                report(session, result.stdout == expected, result.stdout, expected, counters)

def main():
    counters = {"passed": 0, "failed": 0}

    programs = sorted(
        glob.glob("tests/*.txt")
        + glob.glob("tests/ok/*.txt")
        + glob.glob("tests/err/*.txt")
    )
    for path in programs:
        outcome = run_output_test(path)
        if outcome is None:
            continue
        report(path, *outcome, counters)

    for path in sorted(glob.glob("tests/**/*.ast", recursive=True)):
        report(path, *run_ast_test(path), counters)

    for path in sorted(glob.glob("tests/opt/*.txt")):
        for flags, label in (([], "plain"), (["--remove-unused"], "opt")):
            outcome = run_output_test(path, flags)
            if outcome is None:
                continue
            report(f"{path} [{label}]", *outcome, counters)

    for path in sorted(glob.glob("tests/opt/*.astc")):
        report(path, *run_astc_test(path), counters)

    run_binary_tests(counters)
    print(f"\n{counters['passed']} passed, {counters['failed']} failed")
    sys.exit(1 if counters["failed"] else 0)


if __name__ == "__main__":
    main()