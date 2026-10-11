"""The allowed-edits file: parse, check, sanitize (PLAN.md P1.5, P3.2).

The agent may edit exactly one file of flow knobs. Each line is
`KEY = VALUE` (an `export ` prefix is allowed); blank lines and lines that
start with `#` are ignored. Every key must be in the task's knob spec and
every value must be a plain decimal number inside the knob's range. Anything
else makes the whole file invalid: no partial credit, no first-or-last-wins.

The verifier never hands the agent's file to make. It writes its own
sanitized copy (sanitized_mk) with only checked keys and values, so make
syntax such as $(shell ...) can never reach the flow.
"""

import os
import re
import stat
from decimal import Decimal

MAX_BYTES = 4096

_LINE = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*([-+.0-9A-Za-z]+)$")
_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_INT = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_FLOAT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_spec(spec):
    """Raise ValueError if the knob spec is malformed."""
    if not isinstance(spec, dict):
        raise ValueError("knob spec must be an object")
    for key, rule in spec.items():
        if not _KEY.match(key):
            raise ValueError(f"knob spec: bad key {key!r}")
        if not isinstance(rule, dict) or rule.get("type") not in ("int", "float"):
            raise ValueError(f"knob spec {key}: type must be int or float")
        if "default" not in rule:
            raise ValueError(f"knob spec {key}: default missing (null means ORFS leaves it unset)")
        fields = ("min", "max") + (("default",) if rule["default"] is not None else ())
        for field in fields:
            if not _is_number(rule.get(field)):
                raise ValueError(f"knob spec {key}: {field} missing or not a number")
            if rule["type"] == "int" and not float(rule[field]).is_integer():
                raise ValueError(f"knob spec {key}: {field} must be an integer")
        if rule["min"] > rule["max"]:
            raise ValueError(f"knob spec {key}: need min <= max")
        if rule["default"] is not None and not rule["min"] <= rule["default"] <= rule["max"]:
            raise ValueError(f"knob spec {key}: need min <= default <= max")


def _check_value(key, value, rule):
    """Return an error string, or None when the value is legal."""
    if rule["type"] == "int":
        if not _INT.match(value) or value == "-0":
            return f"{key} = {value} is not an integer"
    elif not _FLOAT.match(value) or (value.startswith("-") and Decimal(value) == 0):
        return f"{key} = {value} is not a number"
    low, high = Decimal(str(rule["min"])), Decimal(str(rule["max"]))
    if not low <= Decimal(value) <= high:
        return f"{key} = {value} out of range [{rule['min']}, {rule['max']}]"
    return None


def parse(data, spec):
    """Check an allowed-edits file given as bytes.

    Returns (ok, values, errors). values maps key to the value string exactly
    as written, and is empty whenever errors is not.
    """
    check_spec(spec)
    if len(data) > MAX_BYTES:
        return False, {}, [f"file larger than {MAX_BYTES} bytes"]
    if b"\x00" in data:
        return False, {}, ["file contains a NUL byte"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, {}, ["file is not UTF-8"]
    values, errors = {}, []
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            errors.append(f"line {number}: bad line")
            continue
        key, value = match.groups()
        if key not in spec:
            errors.append(f"line {number}: {key} is not an allowed knob")
            continue
        if key in values:
            errors.append(f"line {number}: {key} set twice")
            continue
        problem = _check_value(key, value, spec[key])
        if problem:
            errors.append(f"line {number}: {problem}")
            continue
        values[key] = value
    if errors:
        return False, {}, errors
    return True, dict(sorted(values.items())), []


def sanitized_mk(values):
    """Makefile text the flow includes: checked keys only, sorted."""
    lines = ["# Written by the Signoff Gym verifier from the checked allowed-edits file."]
    lines += [f"export {key} = {values[key]}" for key in sorted(values)]
    return "\n".join(lines) + "\n"


def read_file(path):
    """Read the allowed-edits file safely. Returns (bytes, None) or (None, error)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, "knobs file missing"
    if stat.S_ISLNK(info.st_mode):
        return None, "knobs file is a symlink"
    if not stat.S_ISREG(info.st_mode):
        return None, "knobs file is not a regular file"
    if info.st_size > MAX_BYTES:
        return None, f"knobs file larger than {MAX_BYTES} bytes"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    return data, None
