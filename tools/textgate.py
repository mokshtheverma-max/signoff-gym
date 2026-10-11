#!/usr/bin/env python3
"""Text gate for every commit (PLAN.md step S0.2).

Fails when a file or commit message contains:
  - U+2014 (em-dash) in UTF-8 text,
  - byte 0x97 in text that is not valid UTF-8 (read as cp1252, where 0x97
    is the em-dash),
  - the banned 45 nm kit name, in any letter case.

A file with a NUL byte is binary and skipped. The banned names are stored
hex-encoded so this file does not contain them.

Usage:
  textgate.py --staged        check the staged content of staged files
  textgate.py --tracked       check the working-tree content of tracked files
  textgate.py --msg FILE      check a commit message file
  textgate.py PATH...         check these files
Exit code 0 when clean, 1 when a problem is found.
"""

import subprocess
import sys

EM_DASH = chr(0x2014)
EM_DASH_UTF8 = EM_DASH.encode("utf-8")
CP1252_EM_DASH = 0x97
BANNED_NAMES = tuple(
    bytes.fromhex(h).decode("ascii")
    for h in ("6e616e67617465", "6672656570646b3435")
)


def _line_of(data, index):
    return data.count(b"\n", 0, index) + 1


def check_bytes(data, label="<data>"):
    """Return a list of problem strings for one blob of bytes."""
    problems = []
    for name in BANNED_NAMES:
        if name in label.lower():
            problems.append(f"{label}: path contains a banned kit name")
    if b"\x00" in data:
        return problems
    lowered = data.lower()
    for name in BANNED_NAMES:
        index = lowered.find(name.encode("ascii"))
        if index >= 0:
            problems.append(
                f"{label}:{_line_of(data, index)}: banned kit name"
            )
    index = data.find(EM_DASH_UTF8)
    if index >= 0:
        problems.append(f"{label}:{_line_of(data, index)}: U+2014 em-dash")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        index = data.find(bytes([CP1252_EM_DASH]))
        if index >= 0:
            problems.append(
                f"{label}:{_line_of(data, index)}: byte 0x97 (cp1252 em-dash)"
            )
    return problems


def _git(args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True
    ).stdout


def staged_blobs():
    names = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"])
    for name in sorted(n for n in names.decode("utf-8").split("\0") if n):
        yield name, _git(["show", f":{name}"])


def tracked_blobs():
    names = _git(["ls-files", "-z"])
    for name in sorted(n for n in names.decode("utf-8").split("\0") if n):
        try:
            with open(name, "rb") as handle:
                yield name, handle.read()
        except FileNotFoundError:
            continue


def file_blobs(paths):
    for path in paths:
        with open(path, "rb") as handle:
            yield path, handle.read()


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--staged":
        blobs = staged_blobs()
    elif argv[0] == "--tracked":
        blobs = tracked_blobs()
    elif argv[0] == "--msg" and len(argv) == 2:
        blobs = file_blobs([argv[1]])
    else:
        blobs = file_blobs(argv)
    problems = []
    count = 0
    for label, data in blobs:
        count += 1
        problems.extend(check_bytes(data, label))
    for problem in problems:
        print(f"textgate: {problem}", file=sys.stderr)
    if problems:
        print(f"textgate: FAIL, {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"textgate: PASS, {count} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
