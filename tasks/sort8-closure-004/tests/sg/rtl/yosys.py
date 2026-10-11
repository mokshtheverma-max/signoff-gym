"""The fixed Yosys scripts of the RTL surface and the checks on their output
(PLAN.md F2.5 B, C and D).

Part of Signoff Gym by Moksh Verma.

Every script here is verifier-owned text: the file names are the staged
names (rtl_00.v, rtl_01.v, ...) the runner or verifier chose, the top
module's name comes from the task's own targets, and nothing the agent
wrote is ever part of a script. The design is read exactly as ORFS's
synthesis reads it (read_verilog -defer -sv, which defines SYNTHESIS).

The elaboration script writes the design as JSON (for the port lock and
the structural checks) and as RTLIL (what the testbench simulates). Pure.

Generator version 2 (PLAN.md V2.5 B) adds one structural rule, registered
ports: every input port but the clock and the reset is read only by
flip-flop data inputs (through a synchronous reset to a constant at most),
and every output port is driven straight by a flip-flop. Version-1 tasks
keep the file as it was (sg/f2v1/rtl/yosys.py).

Generator version 3 (PLAN.md V3.5 A and B): the script runs `memory -nordff`,
so no register is merged into a table's read port and the netlist shows the
registers where the RTL puts them (the v3 flow does the same, SYNTH_ARGS);
and a second rule, every flip-flop clears: with the reset held at 1 and
constants folded, every flip-flop's next value must be the constant 0.
Version-2 tasks keep the file as it was (sg/f2v2/rtl/yosys.py).
"""

import re

STAGED = re.compile(r"rtl_[0-9]{2}\.v")
TOP = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
FILE_ARG = re.compile(r"[A-Za-z0-9_./-]{1,200}")
LATCH = re.compile(r"latch", re.I)
FLOP_TYPES = ("$dff", "$dffe", "$sdff", "$sdffe", "$sdffce", "$adff", "$adffe", "$aldff",
              "$aldffe", "$dffsr", "$dffsre")
WARNING = re.compile(r"^Warning: (.*)$", re.M)
ERROR = re.compile(r"^ERROR: (.*)$", re.M)
# Warnings on this list do not fail the lint (PLAN.md F2.5 B: only with an
# F2.6 entry first). It is empty.
ALLOWED_WARNINGS = ()
# The reset port of every F2 design (a unit test checks every family): the
# registered-ports rule lets it reach flip-flops through their synchronous-
# reset muxes and, like the clock, does not check where else it goes.
RESET = "rst"


def _check_args(files, top, outputs):
    if not files or any(not STAGED.fullmatch(name) for name in files):
        raise ValueError("staged file names must be rtl_NN.v")
    if not TOP.fullmatch(top):
        raise ValueError("bad top module name")
    for path in outputs:
        if not FILE_ARG.fullmatch(path):
            raise ValueError("bad output path")


# Constant folding for the reset rule: no pass here removes a flip-flop.
FOLD = "opt_expr -mux_undef -mux_bool -fine"


def elaborate_script(files, top, json_out, rtlil_out, reset_out, reset=RESET):
    """Read, check and flatten the design; write its JSON (the lint's rules)
    and its RTLIL (the testbench); then hold the reset at 1, fold constants
    and write that netlist as JSON too (the reset rule)."""
    _check_args(files, top, (json_out, rtlil_out, reset_out))
    if not TOP.fullmatch(reset):
        raise ValueError("bad reset port name")
    lines = [f"read_verilog -defer -sv {name}" for name in files]
    lines += [f"hierarchy -check -top {top}", "proc", "flatten", "memory -nordff", "opt_clean", "check",
              f"write_json {json_out}", "opt_clean -purge", f"write_rtlil {rtlil_out}",
              f"connect -set {reset} 1'1", FOLD, "opt_muxtree", FOLD, f"write_json {reset_out}"]
    return "\n".join(lines) + "\n"


def sim_script(rtlil, top, trace, vcd):
    """Replay a witness trace on the elaborated design and write a VCD."""
    _check_args(["rtl_00.v"], top, (rtlil, trace, vcd))
    return (f"read_rtlil {rtlil}\n"
            f"sim -r {trace} -scope {top} -width 10 -vcd {vcd} {top}\n")


def equiv_script(gold_files, gate_files, top, steps):
    """A same-latency equivalence check: the pristine design (gold) against
    the agent's (gate), with Yosys's equiv flow (signals matched by name,
    then simple and inductive proofs over `steps` cycles). gold_files are
    gold_NN.v, gate_files the staged rtl_NN.v. equiv_status reports; it does
    not stop the script, and nothing is graded on it."""
    if not gold_files or any(not re.fullmatch(r"gold_[0-9]{2}\.v", n) for n in gold_files):
        raise ValueError("gold file names must be gold_NN.v")
    _check_args(gate_files, top, ())
    if not isinstance(steps, int) or not 1 <= steps <= 100:
        raise ValueError("bad step count")
    lines = [f"read_verilog -defer -sv {name}" for name in gold_files]
    lines += [f"hierarchy -top {top}", "proc", "flatten", "memory", "opt_clean",
              f"rename {top} gold", "design -stash gold"]
    lines += [f"read_verilog -defer -sv {name}" for name in gate_files]
    lines += [f"hierarchy -top {top}", "proc", "flatten", "memory", "opt_clean",
              f"rename {top} gate", "design -stash gate",
              "design -copy-from gold -as gold gold", "design -copy-from gate -as gate gate",
              "equiv_make gold gate equiv", "hierarchy -top equiv",
              f"equiv_simple -seq {steps}", f"equiv_induct -seq {steps}", "equiv_status"]
    return "\n".join(lines) + "\n"


def log_findings(text):
    """(warnings not allowed, errors) from a Yosys log."""
    warnings = [w.strip() for w in WARNING.findall(text)
                if not any(w.strip().startswith(a) for a in ALLOWED_WARNINGS)]
    return warnings, [e.strip() for e in ERROR.findall(text)]


def _is_flop(kind):
    return kind in FLOP_TYPES or kind.startswith("$_DFF") or kind.startswith("$_SDFF")


def registered_port_problems(module, clock, reset=RESET):
    """The registered-ports rule on one flattened module (PLAN.md V2.5 B).
    Every bit of every input port but the clock and the reset may be read
    only by a flip-flop's data input, directly or through a synchronous-
    reset mux (its select the reset port, its other data input constant)
    whose output is read the same way; every bit of every output port must
    be driven by a flip-flop's Q. One problem per offending port."""
    ports = module.get("ports") or {}
    readers, drivers = {}, {}
    for _, item in sorted((module.get("cells") or {}).items()):
        directions = item.get("port_directions") or {}
        for pin, bits in (item.get("connections") or {}).items():
            for index, bit in enumerate(bits):
                if isinstance(bit, int):
                    table = drivers if directions.get(pin) == "output" else readers
                    table.setdefault(bit, []).append((item, pin, index))
    reset_port = ports.get(reset)
    reset_bits = reset_port.get("bits") if isinstance(reset_port, dict) and reset_port.get(
        "direction") == "input" else None
    reset_bit = reset_bits[0] if isinstance(reset_bits, list) and len(reset_bits) == 1 else None
    input_bits = {bit for p in ports.values() if p.get("direction") == "input" for bit in p.get("bits") or []}
    output_bits = {bit for p in ports.values() if p.get("direction") == "output" for bit in p.get("bits") or []}

    def reset_mux(item, pin):
        connections = item.get("connections") or {}
        other = connections.get("B" if pin == "A" else "A") or []
        return (item.get("type") == "$mux" and pin in ("A", "B") and reset_bit is not None
                and connections.get("S") == [reset_bit] and bool(other)
                and all(isinstance(b, str) for b in other))

    def bad_readers(bit, seen):
        """What, other than a flip-flop's data input, reads this bit."""
        found = {"an output port"} if bit in output_bits else set()
        for item, pin, index in readers.get(bit, []):
            kind = item.get("type", "")
            if _is_flop(kind) and pin == "D":
                continue
            if reset_mux(item, pin):
                out = (item.get("connections") or {}).get("Y") or []
                if index < len(out) and isinstance(out[index], int):
                    if out[index] not in seen:
                        seen.add(out[index])
                        found |= bad_readers(out[index], seen)
                    continue
            found.add(f"{kind} {pin}")
        return found

    problems = []
    for name, item in sorted(ports.items()):
        bits = item.get("bits") or []
        what, bad = set(), 0
        if item.get("direction") == "input" and name not in (clock, reset):
            for bit in bits:
                found = bad_readers(bit, {bit}) if isinstance(bit, int) else set()
                if found:
                    bad += 1
                    what |= found
            if bad:
                problems.append(f"registered ports: input {name}: {bad} of {len(bits)} bits reach "
                                f"{', '.join(sorted(what))} before a flip-flop's data input")
        elif item.get("direction") == "output":
            for bit in bits:
                source = drivers.get(bit, []) if isinstance(bit, int) else []
                if len(source) == 1 and _is_flop(source[0][0].get("type", "")) and source[0][1] == "Q":
                    continue
                bad += 1
                if not isinstance(bit, int):
                    what.add("a constant")
                elif not source:
                    what.add("an input port" if bit in input_bits else "nothing")
                else:
                    what |= {f"{entry.get('type', '')} {pin}" for entry, pin, _ in source}
            if bad:
                problems.append(f"registered ports: output {name}: {bad} of {len(bits)} bits are not driven "
                                f"straight by a flip-flop ({', '.join(sorted(what))})")
    return problems


def netlist_problems(doc, top, clock, reset=RESET):
    """Structural rules on the elaborated design (JSON): no latch, every
    flip-flop clocked by the top's clock port on its rising edge, no
    memory left, no cell type outside Yosys's own internal cells, and
    registered ports (registered_port_problems)."""
    module = (doc.get("modules") or {}).get(top) if isinstance(doc, dict) else None
    if not isinstance(module, dict):
        return [f"the top module {top} is not in the elaborated design"]
    problems = []
    others = sorted(name for name in (doc.get("modules") or {}) if name != top)
    if others:
        problems.append(f"modules left after flattening: {others[:5]}")
    port = (module.get("ports") or {}).get(clock)
    clock_bits = port.get("bits") if isinstance(port, dict) else None
    if not clock_bits or len(clock_bits) != 1 or port.get("direction") != "input":
        return problems + [f"the clock port {clock} is not a one-bit input"]
    for name, cell in sorted((module.get("cells") or {}).items()):
        kind = cell.get("type", "")
        if not kind.startswith("$"):
            problems.append(f"cell {name}: {kind} is not a Yosys internal cell")
        elif LATCH.search(kind):
            problems.append(f"cell {name}: a latch ({kind})")
        elif kind.startswith("$mem"):
            problems.append(f"cell {name}: a memory ({kind}) left unmapped")
        elif kind in FLOP_TYPES or kind.startswith("$_DFF") or kind.startswith("$_SDFF"):
            connections = cell.get("connections") or {}
            polarity = str((cell.get("parameters") or {}).get("CLK_POLARITY", "1"))
            if connections.get("CLK") != clock_bits or polarity.strip("0") != "1":
                problems.append(f"cell {name}: a flip-flop not clocked by {clock} on its rising edge")
        elif kind in ("$print", "$check", "$assert", "$assume", "$cover", "$live", "$fair",
                      "$initstate", "$anyconst", "$anyseq", "$allconst", "$allseq", "$scopeinfo"):
            if kind != "$scopeinfo":
                problems.append(f"cell {name}: {kind} is not allowed")
    return problems + registered_port_problems(module, clock, reset)


def _cleared_bits(cell):
    """For one flip-flop of the netlist with the reset held at 1 and constants
    folded: per bit (least significant first), True when its next value is
    surely 0. A type or a shape this cannot settle is never True."""
    kind = cell.get("type", "")
    connections = cell.get("connections") or {}
    parameters = cell.get("parameters") or {}
    data = connections.get("D") or []
    if kind == "$dff":
        return [bit == "0" for bit in data]
    if kind == "$adff":
        value = str(parameters.get("ARST_VALUE", ""))
        if len(value) != len(data) or set(value) - {"0", "1"}:
            return [False] * len(data)
        values = value[::-1]
        active = "1" if "1" in str(parameters.get("ARST_POLARITY", "1")) else "0"
        if connections.get("ARST") == [active]:
            return [v == "0" for v in values]
        return [bit == "0" and v == "0" for bit, v in zip(data, values)]
    return [False] * len(connections.get("Q") or data)


def reset_problems(doc, reset_doc, top, reset=RESET):
    """The reset rule (PLAN.md V3.5 B), as the instruction promises: at a
    rising edge with the reset high every register of the design clears.
    doc is the lint's netlist, reset_doc the same netlist with the reset held
    at 1 and constants folded (elaborate_script). Every flip-flop of doc must
    be in reset_doc, of the same type, with every bit's next value 0; one
    problem per register that does not clear, naming how many bits fail."""
    module = (doc.get("modules") or {}).get(top) if isinstance(doc, dict) else None
    after = (reset_doc.get("modules") or {}).get(top) if isinstance(reset_doc, dict) else None
    if not isinstance(module, dict) or not isinstance(after, dict):
        return ["reset: the reset check's netlist could not be read"]
    names = {}
    for name, net in sorted((module.get("netnames") or {}).items()):
        if not name.startswith("$") and not net.get("hide_name"):
            for bit in net.get("bits") or []:
                names.setdefault(bit, name)
    folded = after.get("cells") or {}
    reset_port = (module.get("ports") or {}).get(reset) or {}
    reset_bits = reset_port.get("bits") if reset_port.get("direction") == "input" else None
    registers, foreign = {}, set()
    for name, cell in sorted((module.get("cells") or {}).items()):
        if not _is_flop(cell.get("type", "")):
            continue
        q = (cell.get("connections") or {}).get("Q") or []
        register = next((names[bit] for bit in q if bit in names), name)
        arst = (cell.get("connections") or {}).get("ARST")
        if arst is not None and arst != reset_bits:
            foreign.add(register)
        other = folded.get(name)
        if not isinstance(other, dict) or other.get("type") != cell.get("type"):
            cleared = [False] * len(q)
        else:
            cleared = _cleared_bits(other)
            if len(cleared) != len(q):
                cleared = [False] * len(q)
        bad, total = registers.get(register, (0, 0))
        registers[register] = (bad + sum(1 for ok in cleared if not ok), total + len(q))
    return ([f"reset: register {register}: {bad} of {total} bits do not clear when {reset} is high"
             for register, (bad, total) in sorted(registers.items()) if bad]
            + [f"reset: register {register}: an asynchronous reset not driven by {reset}"
               for register in sorted(foreign)])


def equiv_verdict(text, timed_out):
    """The equivalence report line's words: proved, or not proved and why.
    A timeout never counts as proved."""
    if timed_out:
        return "not proved (time limit)"
    found = re.findall(r"Of those cells (\d+) are proven and (\d+) are unproven\.", text)
    if not found:
        return "not proved (the check did not run)"
    proven, unproven = (int(v) for v in found[-1])
    if proven > 0 and unproven == 0:
        return "proved"
    return f"not proved ({unproven} of {proven + unproven} equivalence points unproven)"
