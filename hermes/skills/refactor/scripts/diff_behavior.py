#!/usr/bin/env python3
"""Differential behavior test for a behavior-preserving refactor.

Compares an original module against a refactored one, function by function,
case by case. Two behaviours count as identical when either both return
equal values, or both raise the same exception type with the same message.

Usage:
    diff_behavior.py ORIGINAL.py REFACTORED.py FUNC [FUNC ...] [--cases a,b,c]

Exit code 0 = identical behaviour; 1 = at least one mismatch (printed).
"""
import argparse
import importlib.util
import sys

# Broad default probes for string->int style parsers; pass --cases for
# domain-specific inputs. The literal "None" is Python None.
DEFAULT_CASES = [
    "None", "", "   ", "0", "1", "-1", "80", " 80 ", "080", "12.5",
    "abc", "8 0", "0x50", "1e3", "+5", "-0", "65535", "65536", "007",
    "99999999999999999999",
]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(func, value):
    try:
        return func(value), None
    except Exception as exc:  # compare any raised exception
        return None, exc


def main():
    parser = argparse.ArgumentParser(
        description="Differential behavior test: original vs refactored module.")
    parser.add_argument("original")
    parser.add_argument("refactored")
    parser.add_argument("funcs", nargs="+", help="function name(s) to compare")
    parser.add_argument("--cases", default=None,
                        help="comma-separated case list; 'None' means Python None")
    args = parser.parse_args()

    raw = DEFAULT_CASES if args.cases is None else args.cases.split(",")
    cases = [None if c == "None" else c for c in raw]

    orig = load("original_mod", args.original)
    ref = load("refactored_mod", args.refactored)

    mismatches = 0
    for fn in args.funcs:
        for case in cases:
            ro, eo = run(getattr(orig, fn), case)
            rr, er = run(getattr(ref, fn), case)
            same_value = eo is None and er is None and ro == rr
            same_error = (
                isinstance(eo, Exception)
                and isinstance(er, Exception)
                and type(eo) is type(er)
                and str(eo) == str(er)
            )
            if not (same_value or same_error):
                mismatches += 1
                print(f"MISMATCH {fn}({case!r}):")
                print(f"  original:   {eo!r} / {ro!r}")
                print(f"  refactored: {er!r} / {rr!r}")

    total = len(cases) * len(args.funcs)
    print(f"{total} comparisons, {mismatches} mismatches")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
