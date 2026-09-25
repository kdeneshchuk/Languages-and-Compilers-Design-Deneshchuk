import glob
import subprocess
import sys

OUT_LL = "/tmp/run_tests_out.ll"


def run_output_test(txt_path):
    """Compile, run through lli if it compiled, compare with .expected."""
    exp_path = txt_path[:-4] + ".expected"
    try:
        with open(exp_path) as f:
            expected = f.read().strip()
    except FileNotFoundError:
        return None

    result = subprocess.run(
        ["python3", "compiler.py", txt_path, OUT_LL],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        lli_result = subprocess.run(
            ["lli", OUT_LL],
            capture_output=True, text=True
        )
        actual = lli_result.stdout.strip()
    else:
        actual = result.stderr.strip()

    return actual == expected, actual, expected


def run_ast_test(ast_path):
    """Compare --ast output with the .ast snapshot."""
    txt_path = ast_path[:-4] + ".txt"
    with open(ast_path) as f:
        expected = f.read().strip()

    result = subprocess.run(
        ["python3", "compiler.py", "--ast", txt_path],
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

    print(f"\n{counters['passed']} passed, {counters['failed']} failed")
    sys.exit(1 if counters["failed"] else 0)


if __name__ == "__main__":
    main()
