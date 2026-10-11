"""Power from real switching activity (PLAN.md V3.5 E), the pure parts.

Part of Signoff Gym by Moksh Verma.

OpenSTA's power report, as ORFS runs it, is vectorless: no switching activity is read, so
OpenSTA assumes a default activity on the inputs and propagates it through the logic. Here the
finished design is simulated instead. Yosys reads the kit's liberty cells as models (each
cell's `function` and `ff` groups become AND, OR, NOT and flip-flop gates) and the flow's final
netlist (6_final.v), flattens it and writes it as JSON; `simulate` applies a workload to that
netlist cycle by cycle and counts, for every net bit, how often it changes and how long it is
1; OpenSTA then reloads the finished design as ORFS's finish stage does and reports total power
twice, without and with that activity on every net (POWER_TCL). The first number must equal the
flow's own finish__power__total: the check that OpenSTA loaded the design the flow signed off.

The simulation's rules: sample 2t is cycle t with its inputs (and reset) applied and the clock
low; sample 2t + 1 is the same cycle after the clock's rising edge. Flip-flops start at 0 and
capture their input at each rising edge of the clock. A net's activity is its transitions
between consecutive samples per cycle; its duty is the share of samples at 1. Zero delay, so
no glitches, as SAIF-based power reports count them.

The simulator holds each net's whole waveform as one Python integer (bit s is sample s), so a
gate is one big-integer operation; flip-flop waveforms start at 0 and are recomputed until they
stop changing (each round settles at least one more cycle), in chunks so that a design with
feedback costs at most a chunk's length in rounds. Pure: no file, process or network.
"""

import collections
import math
import random
import re
import time

LIBERTY = "/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
RESET_CYCLES = 2
POWER_CYCLES = 4000
CHUNK = 512
PATH = re.compile(r"[A-Za-z0-9_./-]{1,200}")
TOP = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# The relative difference allowed between OpenSTA's vectorless total here and the flow's
# finish__power__total, which ORFS writes with fewer digits.
VECTORLESS_TOLERANCE = 1e-5

GATE_PINS = {"$_AND_": ("A", "B"), "$_OR_": ("A", "B"), "$_XOR_": ("A", "B"), "$_NAND_": ("A", "B"),
             "$_NOR_": ("A", "B"), "$_XNOR_": ("A", "B"), "$_ANDNOT_": ("A", "B"), "$_ORNOT_": ("A", "B"),
             "$_NOT_": ("A",), "$_BUF_": ("A",), "$_MUX_": ("A", "B", "S")}
GATE_FUNCTIONS = {
    "$_AND_": lambda a, b: a & b, "$_OR_": lambda a, b: a | b, "$_XOR_": lambda a, b: a ^ b,
    "$_NAND_": lambda a, b: 1 - (a & b), "$_NOR_": lambda a, b: 1 - (a | b), "$_XNOR_": lambda a, b: 1 - (a ^ b),
    "$_ANDNOT_": lambda a, b: a & (1 - b), "$_ORNOT_": lambda a, b: a | (1 - b), "$_NOT_": lambda a: 1 - a,
    "$_BUF_": lambda a: a, "$_MUX_": lambda a, b, s: b if s else a,
}
# Flip-flops: no reset, or an asynchronous reset (its active level, the value it sets).
FLOPS = {"$_DFF_P_": None, "$_DFF_PN0_": (0, 0), "$_DFF_PN1_": (0, 1), "$_DFF_PP0_": (1, 0),
         "$_DFF_PP1_": (1, 1)}

Flop = collections.namedtuple("Flop", "kind clock data q reset reset_net")


class PowerError(Exception):
    """The netlist or the report cannot give a power number: never read as a pass."""


class Netlist:
    def __init__(self, gates, flops, ports, netnames):
        self.gates = gates          # [(kind, (input bits), output bit)] in an order that settles
        self.flops = flops          # [Flop]
        self.ports = ports          # {port name: [bits]}
        self.netnames = netnames    # Yosys's netnames of the top module


def gate_script(netlist, top, json_out):
    """The fixed Yosys script: the kit's cells as models, the final netlist, flattened, as JSON.
    Cells with no function (the clock gate) stay empty boxes; simulate refuses a design using one."""
    for path in (netlist, json_out):
        if not PATH.fullmatch(path):
            raise ValueError("bad path")
    if not TOP.fullmatch(top):
        raise ValueError("bad top module name")
    return "\n".join([f"read_liberty -ignore_miss_func {LIBERTY}", f"read_verilog {netlist}",
                      f"hierarchy -check -top {top}", "flatten", f"write_json {json_out}"]) + "\n"


def _bit(raw):
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if raw in ("0", "1"):
        return raw
    raise PowerError(f"an undefined bit {raw!r} in the netlist")


def load_gates(doc, top):
    """The flattened netlist (Yosys JSON) as a Netlist, its gates ordered so each comes after
    the gates that drive it. Refuses a cell it cannot simulate, a combinational loop, an
    undriven net and an asynchronous reset that a flip-flop drives."""
    module = (doc.get("modules") or {}).get(top) if isinstance(doc, dict) else None
    if not isinstance(module, dict):
        raise PowerError(f"the top module {top} is not in the netlist")
    ports = {name: [_bit(b) for b in p.get("bits") or []] for name, p in (module.get("ports") or {}).items()}
    gates, flops = [], []
    for name, cell in sorted((module.get("cells") or {}).items()):
        kind, conn = cell.get("type", ""), cell.get("connections") or {}
        if kind == "$scopeinfo":
            continue
        try:
            if kind in GATE_PINS:
                gates.append((kind, tuple(_bit(conn[p][0]) for p in GATE_PINS[kind]), _bit(conn["Y"][0])))
            elif kind in FLOPS:
                reset = FLOPS[kind]
                flops.append(Flop(kind, _bit(conn["C"][0]), _bit(conn["D"][0]), _bit(conn["Q"][0]), reset,
                                  _bit(conn["R"][0]) if reset is not None else None))
            else:
                raise PowerError(f"cell {name}: {kind} cannot be simulated")
        except (KeyError, IndexError):
            raise PowerError(f"cell {name}: {kind} is missing a pin") from None
    inputs = {b for p, info in (module.get("ports") or {}).items() if info.get("direction") == "input"
              for b in ports[p]}
    driver = {out: index for index, (_, _, out) in enumerate(gates)}
    driven = set(driver) | {f.q for f in flops} | inputs | {"0", "1"}
    for kind, ins, out in gates:
        for bit in ins:
            if bit not in driven:
                raise PowerError(f"an undriven net (bit {bit}) feeds a {kind} gate")
    for f in flops:
        for bit in (f.clock, f.data, f.reset_net):
            if bit is not None and bit not in driven:
                raise PowerError(f"an undriven net (bit {bit}) feeds a flip-flop")
    users = collections.defaultdict(list)
    waiting = [0] * len(gates)
    for index, (_, ins, _) in enumerate(gates):
        for bit in ins:
            if bit in driver:
                users[driver[bit]].append(index)
                waiting[index] += 1
    order = [index for index in range(len(gates)) if waiting[index] == 0]
    for index in order:
        for user in users[index]:
            waiting[user] -= 1
            if waiting[user] == 0:
                order.append(user)
    if len(order) != len(gates):
        raise PowerError("a combinational loop in the netlist")
    ordered = [gates[index] for index in order]
    # Which nets a flip-flop drives, through gates: an asynchronous reset may not be one of them.
    from_flop = {f.q for f in flops}
    for kind, ins, out in ordered:
        if any(bit in from_flop for bit in ins):
            from_flop.add(out)
    for f in flops:
        if f.reset_net is not None and f.reset_net in from_flop:
            raise PowerError("an asynchronous reset driven by a flip-flop cannot be simulated")
    return Netlist(ordered, flops, ports, module.get("netnames") or {})


def workload(inputs, seed, cycles=POWER_CYCLES):
    """The power workload: RESET_CYCLES of reset with every input 0, then `cycles` cycles of
    inputs uniform over each input's range from `seed`. inputs: the model's [(role, width, signed)]."""
    rng = random.Random(seed)
    zeros = {role: 0 for role, _, _ in inputs}
    rows = [(1, dict(zeros)) for _ in range(RESET_CYCLES)]
    for _ in range(cycles):
        values = {}
        for role, width, signed in inputs:
            raw = rng.getrandbits(width)
            values[role] = raw - (1 << width) if signed and raw >> (width - 1) else raw
        rows.append((0, values))
    return rows


def rows_for(stimulus, inputs):
    """A stimulus [(rst, {role: value})] as simulate's rows [(rst, value, ...)] in input order."""
    return [(rst,) + tuple(values[role] for role, _, _ in inputs) for rst, values in stimulus]


def _evaluate(w, gates, mask):
    for kind, ins, out in gates:
        a = w[ins[0]]
        if kind == "$_AND_":
            w[out] = a & w[ins[1]]
        elif kind == "$_OR_":
            w[out] = a | w[ins[1]]
        elif kind == "$_NOT_":
            w[out] = mask ^ a
        elif kind == "$_XOR_":
            w[out] = a ^ w[ins[1]]
        elif kind == "$_BUF_":
            w[out] = a
        elif kind == "$_MUX_":
            s = w[ins[2]]
            w[out] = (a & (mask ^ s)) | (w[ins[1]] & s)
        elif kind == "$_NAND_":
            w[out] = mask ^ (a & w[ins[1]])
        elif kind == "$_NOR_":
            w[out] = mask ^ (a | w[ins[1]])
        elif kind == "$_XNOR_":
            w[out] = mask ^ (a ^ w[ins[1]])
        elif kind == "$_ANDNOT_":
            w[out] = a & (mask ^ w[ins[1]])
        else:  # $_ORNOT_
            w[out] = a | (mask ^ w[ins[1]])


def simulate(netlist, rows, port_widths, clock="clk", reset="rst", chunk=CHUNK, deadline=None):
    """Run rows [(rst, value per input port, ...)] through the netlist. port_widths gives the
    input ports in row order: [(port name, width)]. Returns ({bit: (transitions, samples at
    1)}, cycles) for every net bit the netlist drives or reads. Past `deadline` (a
    time.monotonic() value) it stops with PowerError: a cap, never a number."""
    ports = netlist.ports
    try:
        clock_bit, reset_bit = ports[clock][0], ports[reset][0]
        in_bits = [ports[name][:width] for name, width in port_widths]
    except (KeyError, IndexError):
        raise PowerError("a port of the workload is not in the netlist") from None
    counts = collections.defaultdict(lambda: [0, 0])
    last = {}
    state = {f.q: 0 for f in netlist.flops}
    for start in range(0, len(rows), chunk):
        part = rows[start:start + chunk]
        n = len(part)
        samples = 2 * n
        mask = (1 << samples) - 1
        even = int("01" * n, 2)
        odd = even << 1
        w = {"0": 0, "1": mask, clock_bit: odd}
        reset_wave = 0
        for t, row in enumerate(part):
            if row[0]:
                reset_wave |= 3 << (2 * t)
        w[reset_bit] = reset_wave
        for column, bits in enumerate(in_bits, 1):
            for i, bit in enumerate(bits):
                wave = 0
                for t, row in enumerate(part):
                    if (row[column] >> i) & 1:
                        wave |= 3 << (2 * t)
                w[bit] = wave
        q = {f.q: 0 for f in netlist.flops}
        for rounds in range(n + 2):
            if deadline is not None and time.monotonic() > deadline:
                raise PowerError("the power simulation passed its time cap")
            w.update(q)
            _evaluate(w, netlist.gates, mask)
            new = {}
            for f in netlist.flops:
                if w[f.clock] != odd:
                    raise PowerError("a flip-flop is not clocked by the clock itself")
                captured = w[f.data] & even
                at_edges = captured << 1          # odd samples: the input just before the edge
                held = captured << 2              # the next even sample holds it
                carry = state[f.q]
                if f.reset is None:
                    wave = (at_edges | held | carry) & mask
                else:
                    active, value = f.reset
                    on = w[f.reset_net] if active else mask ^ w[f.reset_net]
                    level = mask if value else 0
                    # The reset blocks an edge when it is on just before it or after it.
                    blocked = (on | ((on & even) << 1)) & odd
                    keep = (blocked << 1) & even & mask
                    forced = (on | blocked | keep) & mask
                    normal = (at_edges | held | carry) & mask
                    wave = (normal & (mask ^ forced)) | (level & forced)
                new[f.q] = wave
            if new == q:
                break
            q = new
        else:
            raise PowerError("the flip-flops reached no fixed point")
        for bit, wave in w.items():
            if not isinstance(bit, int):
                continue
            before = last.get(bit)
            previous = ((wave << 1) | (before if before is not None else 0)) & mask
            flips = wave ^ previous
            if before is None:
                flips &= mask ^ 1
            entry = counts[bit]
            entry[0] += bin(flips).count("1")
            entry[1] += bin(wave).count("1")
            last[bit] = (wave >> (samples - 1)) & 1
        for f in netlist.flops:
            state[f.q] = (q[f.q] >> (samples - 1)) & 1
    return {bit: tuple(entry) for bit, entry in counts.items()}, len(rows)


def net_activity(netlist, counts, cycles):
    """{net name as OpenSTA prints it: (transitions per cycle, share of time at 1)} for every
    net of the netlist itself (not a flattened cell's own wire, not a Yosys internal name)."""
    out = {}
    for name, net in sorted(netlist.netnames.items()):
        # A flattened cell model's own wires are hidden, start with $ or carry hdlname; a real net's
        # name may hold a dot (a submodule's net, as OpenROAD writes it, e.g. cs0.s[0]).
        if name.startswith("$") or net.get("hide_name") or "hdlname" in (net.get("attributes") or {}):
            continue
        bits = net.get("bits") or []
        offset = net.get("offset", 0) or 0
        for index, bit in enumerate(bits):
            label = name if len(bits) == 1 else f"{name}[{index + offset}]"
            if bit in ("0", "1"):
                out[label] = (0.0, 1.0 if bit == "1" else 0.0)
            elif bit in counts:
                transitions, ones = counts[bit]
                out[label] = (transitions / cycles, ones / (2 * cycles))
    return out


def activity_text(activity):
    """The activity file OpenSTA's script reads as data: one net per line, activity, duty and
    name, separated by tabs, sorted by name. Nothing in it is ever run."""
    lines = []
    for name, (activity_value, duty) in sorted(activity.items()):
        if any(ch in name for ch in "\t\n\r") or not name:
            raise PowerError(f"a net name the activity file cannot hold: {name!r}")
        lines.append(f"{activity_value:.9g}\t{duty:.9g}\t{name}\n")
    return "".join(lines)


# OpenSTA, run by ORFS's `make run` (SG_POWER_ACTIVITY names the activity file). Each form loads
# a stage's database as that ORFS stage reports it: the finished design (finish) or the design
# after global routing (a preview's numbers).
_LOAD = {
    "final": ("# Loads the finished design as ORFS's finish stage does, reports power without activity\n"
              "# (it must equal finish__power__total), then sets every net's activity and duty from the\n"
              "# file SG_POWER_ACTIVITY (data: activity, duty, name per line) on its drivers and reports again.\n"
              "source $::env(SCRIPTS_DIR)/load.tcl\n"
              "load_design 6_1_fill.odb 6_1_fill.sdc\n"
              "set_propagated_clock [all_clocks]\n"
              "read_spef $::env(RESULTS_DIR)/6_final.spef\n"),
    "grt": ("# Loads the design after global routing as ORFS's global-route stage reports it, reports power\n"
            "# without activity (it must equal globalroute__power__total), then with the activity of\n"
            "# SG_POWER_ACTIVITY (data: activity, duty, name per line) on every net's drivers.\n"
            "source $::env(SCRIPTS_DIR)/load.tcl\n"
            "load_design 5_1_grt.odb 5_1_grt.sdc\n"
            "set_propagated_clock [all_clocks]\n"
            "estimate_parasitics -global_routing\n"),
}
_BODY = r"""proc sg_total {} {
  set args {}
  sta::parse_key_args "sg_total" args keys {-corner} flags {}
  return [lrange [sta::design_power [sta::parse_scene keys]] 0 3]
}
puts "SG_POWER_VECTORLESS [sg_total]"
set sg_act [dict create]
set sg_file [open $::env(SG_POWER_ACTIVITY) r]
while {[gets $sg_file sg_line] >= 0} {
  set sg_fields [split $sg_line "\t"]
  if {[llength $sg_fields] == 3} {
    dict set sg_act [lindex $sg_fields 2] [lrange $sg_fields 0 1]
  }
}
close $sg_file
set sg_clock [lindex [all_clocks] 0]
set sg_block [ord::get_db_block]
set sg_annotated 0
set sg_missing 0
set sg_supply 0
foreach sg_net [get_nets *] {
  set sg_name [get_full_name $sg_net]
  if {![dict exists $sg_act $sg_name]} {
    # Never ask a supply net for its pins (OpenROAD stops on it): its signal type says what it is.
    set sg_db [$sg_block findNet $sg_name]
    if {$sg_db != "NULL" && [lsearch -exact {POWER GROUND} [$sg_db getSigType]] >= 0} {
      incr sg_supply
    } else {
      incr sg_missing
    }
    continue
  }
  lassign [dict get $sg_act $sg_name] sg_activity sg_duty
  set sg_ports [get_ports -quiet -of_objects $sg_net -filter "direction==input"]
  if {[llength $sg_ports]} {
    set_power_activity -input_ports $sg_ports -activity $sg_activity -duty $sg_duty -clock $sg_clock
  }
  set sg_drivers [get_pins -quiet -of_objects $sg_net -filter "direction==output"]
  if {[llength $sg_drivers]} {
    set_power_activity -pins $sg_drivers -activity $sg_activity -duty $sg_duty -clock $sg_clock
  }
  incr sg_annotated
}
puts "SG_POWER_NETS annotated $sg_annotated missing $sg_missing supply $sg_supply"
puts "SG_POWER_ACTIVITY [sg_total]"
"""


def power_tcl(stage):
    """The verifier-owned OpenSTA script for a stage: "final" (graded) or "grt" (a preview)."""
    if stage not in _LOAD:
        raise ValueError(f"no power script for stage {stage!r}")
    return "# Signoff Gym: total power with real switching activity (PLAN.md V3.5 E).\n" + _LOAD[stage] + _BODY


POWER_TCL = power_tcl("final")


def netlist_tcl():
    """The script that writes a preview's netlist after global routing (SG_POWER_NETLIST names it),
    as ORFS's finish stage writes 6_final.v: without the physical-only cells."""
    return ("# Signoff Gym: the netlist after global routing, for a preview's power (PLAN.md V3.5 E).\n"
            "source $::env(SCRIPTS_DIR)/load.tcl\n"
            "load_design 5_1_grt.odb 5_1_grt.sdc\n"
            "write_verilog $::env(SG_POWER_NETLIST) -remove_cells [find_physical_only_masters]\n")


_REPORT = {"vectorless": re.compile(r"^SG_POWER_VECTORLESS (.*)$", re.M),
           "activity": re.compile(r"^SG_POWER_ACTIVITY (.*)$", re.M)}
_NETS = re.compile(r"^SG_POWER_NETS annotated (\d+) missing (\d+) supply (\d+)$", re.M)


def parse_report(text):
    """The numbers POWER_TCL printed: {"vectorless": [internal, switching, leakage, total],
    "activity": [...], "annotated", "missing", "supply"}; a part not found is None."""
    report = {}
    for key, pattern in _REPORT.items():
        found = pattern.findall(text or "")
        values = None
        if len(found) == 1:
            try:
                values = [float(v) for v in found[0].split()]
            except ValueError:
                values = None
            if values is not None and len(values) != 4:
                values = None
        report[key] = values
    nets = _NETS.findall(text or "")
    report["annotated"], report["missing"], report["supply"] = (
        tuple(int(v) for v in nets[0]) if len(nets) == 1 else (None, None, None))
    return report


def check(report, flow_total):
    """(total power in watts from real activity, or None; problems). It counts only when the
    vectorless total equals the flow's finish__power__total and every driven net was annotated."""
    problems = []
    vectorless, activity = report.get("vectorless"), report.get("activity")
    finite = lambda v: isinstance(v, float) and math.isfinite(v) and v > 0  # noqa: E731
    if not vectorless or not finite(vectorless[3]):
        problems.append("power: the vectorless total was not reported")
    elif not (isinstance(flow_total, (int, float)) and flow_total > 0
              and abs(vectorless[3] - flow_total) <= VECTORLESS_TOLERANCE * flow_total):
        problems.append(f"power: the reloaded design's vectorless total {vectorless[3]!r} does not match the "
                        f"flow's {flow_total!r}")
    if not activity or not finite(activity[3]):
        problems.append("power: the total with activity was not reported")
    if not report.get("annotated"):
        problems.append("power: no net was annotated")
    if report.get("missing") != 0:
        problems.append(f"power: {report.get('missing')} nets have no activity")
    return (None, problems) if problems else (activity[3], [])
