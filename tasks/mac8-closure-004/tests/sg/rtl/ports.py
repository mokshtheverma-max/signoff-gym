"""The locked port list of an F2 task's top module (PLAN.md F2.5 C).

Part of Signoff Gym by Moksh Verma.

A port list is [(name, direction, width, signed)]. Its canonical text is
the top's name, then one line per port in byte order of the names; the lock
is the SHA-256 of that text. Pure.
"""

import hashlib


def canonical(top, ports):
    lines = [f"top {top}"]
    for name, direction, width, signed in sorted(ports, key=lambda p: p[0].encode("utf-8")):
        lines.append(f"{direction} {name} {int(width)} {'signed' if signed else 'unsigned'}")
    return "\n".join(lines) + "\n"


def lock(top, ports):
    """The SHA-256 lock of a top module's ports."""
    return hashlib.sha256(canonical(top, ports).encode("utf-8")).hexdigest()


def from_design(design):
    """The port list of a generated Design (sg/gen/families.py)."""
    return [(name, direction, width, signed) for _, name, direction, width, signed in design.ports]


def from_json(doc, top):
    """(port list, problems) of the top module in Yosys's write_json output."""
    module = (doc.get("modules") or {}).get(top) if isinstance(doc, dict) else None
    if not isinstance(module, dict):
        return None, [f"the top module {top} is not in the elaborated design"]
    ports, problems = [], []
    for name, port in sorted((module.get("ports") or {}).items()):
        direction = port.get("direction")
        bits = port.get("bits")
        if direction not in ("input", "output") or not isinstance(bits, list) or not bits:
            problems.append(f"port {name}: direction {direction!r} or width not allowed")
            continue
        ports.append((name, direction, len(bits), bool(port.get("signed"))))
    return ports, problems
