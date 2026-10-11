#!/usr/bin/env python3
"""Check a Signoff Gym knobs file (PLAN.md P3.2, R2-A2).

The counted flow runner imports check() before every run. The grader applies
the same rules with its own checker (sg/knobs.py); a unit test runs both on
the same cases and requires the same answers, and a file this one rejects
fails there too.

Usage: check_knobs.py KNOBS_FILE SPEC_JSON
Prints the checked settings as makefile lines on stdout and exits 0, or
prints every problem on stderr and exits 1.
"""

import json
import re
import sys
from decimal import Decimal

LIMIT = 4096
LINE = re.compile(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*([-+.0-9A-Za-z]+)")
INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)")
DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


def problem_with(key, text, rule):
    pattern, word = (INTEGER, "an integer") if rule["type"] == "int" else (DECIMAL, "a number")
    if not pattern.fullmatch(text) or (text.startswith("-") and Decimal(text) == 0):
        return f"{key} = {text} is not {word}"
    if not Decimal(str(rule["min"])) <= Decimal(text) <= Decimal(str(rule["max"])):
        return f"{key} = {text} out of range [{rule['min']}, {rule['max']}]"
    return None


def check(raw, spec):
    """Return (settings dict, problems list)."""
    if len(raw) > LIMIT:
        return {}, [f"file larger than {LIMIT} bytes"]
    if 0 in raw:
        return {}, ["file contains a NUL byte"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {}, ["file is not UTF-8"]
    settings, problems = {}, []
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.strip()
        if line == "" or line[0] == "#":
            continue
        found = LINE.fullmatch(line)
        if found is None:
            problems.append(f"line {number}: bad line")
        elif found.group(1) not in spec:
            problems.append(f"line {number}: {found.group(1)} is not an allowed knob")
        elif found.group(1) in settings:
            problems.append(f"line {number}: {found.group(1)} set twice")
        else:
            issue = problem_with(found.group(1), found.group(2), spec[found.group(1)])
            if issue:
                problems.append(f"line {number}: {issue}")
            else:
                settings[found.group(1)] = found.group(2)
    return ({}, problems) if problems else (settings, [])


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(argv[0], "rb") as handle:
            raw = handle.read(LIMIT + 1)
    except OSError as error:
        print(f"knobs file: {error.strerror}", file=sys.stderr)
        return 1
    with open(argv[1], encoding="utf-8") as handle:
        spec = json.load(handle)
    settings, problems = check(raw, spec)
    for item in problems:
        print(f"knobs.mk: {item}", file=sys.stderr)
    if problems:
        return 1
    print("# Checked copy of /workspace/knobs.mk, written by run_flow.")
    for key in sorted(settings):
        print(f"export {key} = {settings[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
