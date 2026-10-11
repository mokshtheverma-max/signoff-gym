"""The registered-ports rule on the finished netlist (RTL grader version 4, PLAN.md P.6 4 Oct 19:51).

Part of Signoff Gym by Moksh Verma.

The lint checks the rule on the RTL before any flow (sg/rtl/yosys.py, registered_port_problems) and
the flow keeps the registers where the lint saw them (SYNTH_ARGS = -nordff). This checks the chip the
flow built: the finished netlist (6_final.v) read with the kit's cells as models and flattened, the
JSON the power step already writes (sg/rtl/power.py gate_script).

  - Every bit of every input port but the clock and the reset reaches only flip-flops' data inputs,
    through buffers and inverters, and through at most one two-input AND or OR type gate whose other
    input comes only from the reset (a synchronous reset written as logic). Any other gate, a
    flip-flop's clock, reset or enable pin, an output port, or no flip-flop at all is a problem.
  - Every bit of every output port is driven by a flip-flop's output through buffers and inverters
    only. Logic, a constant, an input port or no driver is a problem.

A cell this does not know (not one of Yosys's own gate and flip-flop cells, such as an empty box for a
cell with no function in the kit) is never passed. One problem per offending port side. Pure.
"""

TRANSPARENT = ("$_BUF_", "$_NOT_")
# A synchronous reset to a constant, as the lint's rule allows it, maps to one of these with the reset.
RESET_GATES = ("$_AND_", "$_OR_", "$_NAND_", "$_NOR_", "$_ANDNOT_", "$_ORNOT_")
OUTPUT_PINS = ("Y", "Q")


def _is_flop(kind):
    return kind.startswith("$_") and ("DFF" in kind or "DLATCH" in kind)


def check(doc, top, clock="clk", reset="rst"):
    """(problems, {"inputs_checked": n, "outputs_checked": m}); counts are None without the top."""
    module = (doc.get("modules") or {}).get(top) if isinstance(doc, dict) else None
    if not isinstance(module, dict):
        return [f"netlist ports: the top module {top} is not in the netlist"], None
    ports = module.get("ports") or {}
    cells = [(cell.get("type", ""), cell.get("connections") or {})
             for _, cell in sorted((module.get("cells") or {}).items()) if cell.get("type") != "$scopeinfo"]
    readers, driver = {}, {}
    for kind, conn in cells:
        for pin, bits in conn.items():
            outputs = ("Q",) if _is_flop(kind) else OUTPUT_PINS
            if pin in outputs and kind.startswith("$_"):
                for bit in bits:
                    driver[bit] = (kind, pin, conn)
            else:
                for bit in bits:
                    readers.setdefault(bit, []).append((kind, pin, conn))
        if not kind.startswith("$_"):
            # A cell of unknown shape: every pin may read or drive, so each pin is both.
            for bits in conn.values():
                for bit in bits:
                    driver.setdefault(bit, (kind, None, conn))
    reset_port = ports.get(reset) or {}
    reset_like = set(reset_port.get("bits") or []) if reset_port.get("direction") == "input" else set()
    grew = True
    while grew:
        grew = False
        for kind, conn in cells:
            if kind in TRANSPARENT and conn.get("A", [None])[0] in reset_like and conn.get("Y", [None])[0] not in reset_like:
                reset_like.add(conn["Y"][0])
                grew = True
    output_bits = {bit for p in ports.values() if p.get("direction") == "output" for bit in p.get("bits") or []}
    input_bits = {bit for p in ports.values() if p.get("direction") == "input" for bit in p.get("bits") or []}

    def walk_in(bit):
        found, reached, stack, seen = set(), 0, [(bit, 0)], set()
        while stack:
            here, resets = stack.pop()
            if (here, resets) in seen:
                continue
            seen.add((here, resets))
            if here in output_bits:
                found.add("drives an output port")
            for kind, pin, conn in readers.get(here, []):
                if _is_flop(kind):
                    if pin == "D":
                        reached += 1
                    else:
                        found.add("reaches a flip-flop's clock, reset or enable pin")
                elif kind in TRANSPARENT and pin == "A":
                    stack.append((conn.get("Y", [None])[0], resets))
                elif (kind in RESET_GATES and resets == 0
                      and all(b in reset_like for p, bits in conn.items() if p not in ("Y",) for b in bits
                              if b != here)):
                    stack.append((conn.get("Y", [None])[0], 1))
                else:
                    found.add(f"read by logic ({kind})")
        if not reached and not found:
            found.add("reaches no flip-flop")
        return found

    def walk_out(bit):
        here = bit
        for _ in range(256):
            if isinstance(here, str):
                return "a constant"
            if here in input_bits:
                return "an input port"
            source = driver.get(here)
            if source is None:
                return "nothing"
            kind, pin, conn = source
            if _is_flop(kind) and pin == "Q":
                return None
            if kind in TRANSPARENT:
                here = conn.get("A", [None])[0]
                continue
            return f"logic ({kind})"
        return "a chain of 256 buffers"

    problems, n_in, n_out = [], 0, 0
    for name, info in sorted(ports.items()):
        bits = info.get("bits") or []
        if info.get("direction") == "input" and name not in (clock, reset):
            what, bad = set(), 0
            for bit in bits:
                n_in += 1
                found = walk_in(bit) if not isinstance(bit, str) else {"a constant"}
                if found:
                    bad += 1
                    what |= found
            if bad:
                problems.append(f"netlist ports: input {name}: {bad} of {len(bits)} bits "
                                f"{', '.join(sorted(what))} before a flip-flop")
        elif info.get("direction") == "output":
            what, bad = set(), 0
            for bit in bits:
                n_out += 1
                why = walk_out(bit)
                if why:
                    bad += 1
                    what.add(why)
            if bad:
                problems.append(f"netlist ports: output {name}: {bad} of {len(bits)} bits not driven straight by a "
                                f"flip-flop ({', '.join(sorted(what))})")
    return problems, {"inputs_checked": n_in, "outputs_checked": n_out}
