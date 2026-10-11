"""Power from real switching activity (PLAN.md V3.5 E): the pure parts in sg/rtl/power.py.

The waveform simulator is checked against a plain sample-by-sample simulator written here, on
hand-made netlists and on random ones (with and without feedback, with asynchronous resets),
so every count it reports is the count a straightforward simulation gives.
"""

import random

import pytest

from sg.rtl import power


# Netlists in the shape of Yosys's write_json after `flatten` (fine-grained cells only).

def _module(cells, ports, netnames=None):
    return {"modules": {"top": {"ports": ports, "cells": cells, "netnames": netnames or {}}}}


def _cell(kind, **pins):
    outs = {"Y", "Q"}
    return {"type": kind, "connections": {p: [b] for p, b in pins.items()},
            "port_directions": {p: ("output" if p in outs else "input") for p in pins}}


PORTS = {"clk": {"direction": "input", "bits": [2]}, "rst": {"direction": "input", "bits": [3]},
         "a": {"direction": "input", "bits": [4, 5]}, "y": {"direction": "output", "bits": [9]}}


def _reference(netlist, rows, widths, chunk=None):
    """Sample-by-sample simulation of the same rules: sample 2t is cycle t's inputs with the
    clock low, 2t + 1 after the rising edge; flip-flops start at 0."""
    ports = netlist.ports
    clock, reset = ports["clk"][0], ports["rst"][0]
    value = {"0": 0, "1": 1}
    state = {f.q: 0 for f in netlist.flops}
    counts, last = {}, {}

    def settle():
        for _ in range(4):
            for f in netlist.flops:
                value[f.q] = state[f.q]
            for kind, ins, out in netlist.gates:
                a = [value[i] for i in ins]
                value[out] = power.GATE_FUNCTIONS[kind](*a)
            changed = False
            for f in netlist.flops:
                if f.reset is not None:
                    active, level = f.reset
                    if value[f.reset_net] == active and state[f.q] != level:
                        state[f.q] = level
                        changed = True
            if not changed:
                return

    def sample():
        for bit, v in value.items():
            if not isinstance(bit, int):
                continue
            tc, ones = counts.get(bit, (0, 0))
            if bit in last and last[bit] != v:
                tc += 1
            counts[bit] = (tc, ones + v)
            last[bit] = v

    for row in rows:
        value[reset] = row[0]
        for (name, width), word in zip(widths, row[1:]):
            for i, bit in enumerate(ports[name][:width]):
                value[bit] = (word >> i) & 1
        value[clock] = 0
        settle()
        sample()
        before = {f.q: value[f.clock] for f in netlist.flops}
        data = {f.q: value[f.data] for f in netlist.flops}
        value[clock] = 1
        settle()
        for f in netlist.flops:
            if before[f.q] == 0 and value[f.clock] == 1:
                if f.reset is None or value[f.reset_net] != f.reset[0]:
                    state[f.q] = data[f.q]
        settle()
        sample()
    return counts


def _rows(n, seed, rst_cycles=2):
    rng = random.Random(seed)
    return [(1 if t < rst_cycles else 0, rng.getrandbits(2)) for t in range(n)]


def test_a_register_follows_its_input_one_cycle_later():
    cells = {"ff": _cell("$_DFF_P_", C=2, D=4, Q=9)}
    net = power.load_gates(_module(cells, PORTS), "top")
    rows = [(0, 1), (0, 0), (0, 1), (0, 1), (0, 0)]
    counts, cycles = power.simulate(net, rows, [("a", 2)])
    assert cycles == 5
    # q: samples 0..9 = 0,1, 1,0, 0,1, 1,1, 1,0 -> 4 transitions, 6 ones
    assert counts[9] == (4, 6)
    # the clock toggles at every sample after the first
    assert counts[2] == (9, 5)
    assert counts == _reference(net, rows, [("a", 2)])


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 512])
def test_feedback_settles_across_chunks(chunk):
    # q <= ~q: a toggle flip-flop; its next state depends on itself through an inverter.
    cells = {"ff": _cell("$_DFF_P_", C=2, D=10, Q=9), "inv": _cell("$_NOT_", A=9, Y=10)}
    net = power.load_gates(_module(cells, PORTS), "top")
    rows = [(0, 0)] * 9
    counts, _ = power.simulate(net, rows, [("a", 2)], chunk=chunk)
    assert counts == _reference(net, rows, [("a", 2)])
    assert counts[9][0] == 9   # it changes at every rising edge: 9 times, held between


def test_an_asynchronous_reset_clears_at_once_and_holds_to_the_next_edge():
    cells = {"ff": _cell("$_DFF_PN0_", C=2, D=4, R=11, Q=9), "inv": _cell("$_NOT_", A=3, Y=11)}
    net = power.load_gates(_module(cells, PORTS), "top")
    rows = [(0, 1), (1, 1), (0, 1), (0, 0), (1, 1), (0, 1)]
    assert power.simulate(net, rows, [("a", 2)])[0] == _reference(net, rows, [("a", 2)])


@pytest.mark.parametrize("seed", range(120))
def test_random_netlists_match_a_plain_simulation(seed):
    rng = random.Random(seed)
    sources = [4, 5, 3]          # the inputs and the reset
    from_inputs = [4, 5, 3]      # nets no flip-flop drives: where most asynchronous resets come from
    cells, next_bit = {}, 20
    flops = []
    for index in range(rng.randint(1, 6)):
        q = next_bit
        next_bit += 1
        flops.append(q)
        sources.append(q)
    for index in range(rng.randint(3, 40)):
        kind = rng.choice(sorted(power.GATE_FUNCTIONS))
        pins = {p: rng.choice(sources) for p in power.GATE_PINS[kind]}
        cells[f"g{index}"] = _cell(kind, Y=next_bit, **pins)
        if all(bit in from_inputs for bit in pins.values()):
            from_inputs.append(next_bit)
        sources.append(next_bit)
        next_bit += 1
    for index, q in enumerate(flops):
        kind = rng.choice(["$_DFF_P_", "$_DFF_P_", "$_DFF_PN0_", "$_DFF_PP0_", "$_DFF_PN1_", "$_DFF_PP1_"])
        pins = {"C": 2, "D": rng.choice(sources), "Q": q}
        if kind != "$_DFF_P_":
            pins["R"] = rng.choice(from_inputs) if rng.random() < 0.9 else rng.choice(sources[3:])
        cells[f"f{index}"] = _cell(kind, **pins)
    ports = dict(PORTS, y={"direction": "output", "bits": [sources[-1]]})
    try:
        net = power.load_gates(_module(cells, ports), "top")
    except power.PowerError as error:
        # an asynchronous reset driven by a flip-flop is refused, never guessed
        assert "asynchronous reset" in str(error)
        return
    rows = _rows(rng.randint(1, 40), seed)
    chunk = rng.choice([1, 3, 8, 512])
    try:
        got = power.simulate(net, rows, [("a", 2)], chunk=chunk)[0]
    except power.PowerError as error:
        assert "fixed point" in str(error)
        return
    assert got == _reference(net, rows, [("a", 2)])


def test_an_asynchronous_reset_driven_by_a_flip_flop_is_refused():
    cells = {"f1": _cell("$_DFF_P_", C=2, D=4, Q=9), "f2": _cell("$_DFF_PN0_", C=2, D=5, R=9, Q=10)}
    with pytest.raises(power.PowerError, match="asynchronous reset"):
        power.load_gates(_module(cells, PORTS), "top")


def test_unsupported_cells_loops_and_clocks_are_refused():
    with pytest.raises(power.PowerError, match="cannot be simulated"):
        power.load_gates(_module({"l": _cell("$_DLATCH_P_", E=2, D=4, Q=9)}, PORTS), "top")
    loop = {"a": _cell("$_NOT_", A=10, Y=11), "b": _cell("$_NOT_", A=11, Y=10)}
    with pytest.raises(power.PowerError, match="combinational loop"):
        power.load_gates(_module(loop, PORTS), "top")
    other = {"ff": _cell("$_DFF_P_", C=4, D=5, Q=9)}
    net = power.load_gates(_module(other, PORTS), "top")
    with pytest.raises(power.PowerError, match="not clocked by the clock"):
        power.simulate(net, [(0, 1), (0, 2)], [("a", 2)])
    undriven = {"g": _cell("$_AND_", A=4, B=77, Y=9)}
    with pytest.raises(power.PowerError, match="undriven"):
        power.simulate(power.load_gates(_module(undriven, PORTS), "top"), [(0, 1)], [("a", 2)])
    with pytest.raises(power.PowerError, match="top module"):
        power.load_gates({"modules": {}}, "top")


def test_net_activity_names_every_net_as_opensta_does_and_counts_constants():
    # u1.Y is a wire of a flattened cell model: Yosys gives every such wire the hdlname attribute.
    # cs0.s[0] is a real net of the finished netlist, as OpenROAD writes a submodule's net (an
    # escaped name with a dot and brackets); OpenSTA's get_full_name prints it exactly so.
    netnames = {"a": {"bits": [4, 5], "offset": 0}, "y": {"bits": [9]}, "n_x[3]": {"bits": [9]},
                "w": {"bits": [4, 5], "offset": 2}, "tie": {"bits": ["1"]}, "low": {"bits": ["0"]},
                "u1.Y": {"bits": [9], "attributes": {"hdlname": "u1 Y"}}, "cs0.s[0]": {"bits": [5]},
                "$flatten\\u1.$auto$x$1": {"bits": [9], "hide_name": 1}}
    cells = {"ff": _cell("$_DFF_P_", C=2, D=4, Q=9)}
    net = power.load_gates(_module(cells, PORTS, netnames), "top")
    counts = {4: (3, 4), 5: (0, 0), 9: (2, 6)}
    found = power.net_activity(net, counts, 4)
    assert found["a[0]"] == (0.75, 0.5) and found["a[1]"] == (0.0, 0.0)
    assert found["w[2]"] == found["a[0]"] and found["w[3]"] == found["a[1]"]
    assert found["y"] == found["n_x[3]"] == (0.5, 0.75)
    assert found["tie"] == (0.0, 1.0) and found["low"] == (0.0, 0.0)
    assert "u1.Y" not in found      # a flattened cell's own wire, not a net of the netlist
    assert not any(name.startswith("$") for name in found)
    assert found["cs0.s[0]"] == found["a[1]"]   # a dotted real net is annotated like any other


def test_the_activity_file_is_plain_data_sorted_by_name():
    text = power.activity_text({"b[1]": (0.5, 0.25), "a": (1.0, 0.5)})
    assert text == "1\t0.5\ta\n0.5\t0.25\tb[1]\n"
    with pytest.raises(power.PowerError):
        power.activity_text({"bad\tname": (0.5, 0.5)})


def test_the_workload_is_reset_then_random_and_the_same_for_the_same_seed():
    inputs = [("a", 4, True), ("b", 3, False)]
    rows = power.workload(inputs, 7, 50)
    assert len(rows) == power.RESET_CYCLES + 50
    assert all(rst == 1 for rst, _ in rows[:power.RESET_CYCLES])
    assert all(rst == 0 for rst, _ in rows[power.RESET_CYCLES:])
    assert rows == power.workload(inputs, 7, 50) and rows != power.workload(inputs, 8, 50)
    assert all(-8 <= v["a"] < 8 and 0 <= v["b"] < 8 for _, v in rows)


def test_the_gate_script_reads_the_kit_cells_and_the_netlist_and_refuses_other_names():
    lines = power.gate_script("6_final.v", "m_top", "gate.json").splitlines()
    assert lines == [f"read_liberty -ignore_miss_func {power.LIBERTY}", "read_verilog 6_final.v",
                     "hierarchy -check -top m_top", "flatten", "write_json gate.json"]
    for bad in (("6_final.v; x", "m_top", "gate.json"), ("6_final.v", "m top", "gate.json"),
                ("6_final.v", "m_top", "g json")):
        with pytest.raises(ValueError):
            power.gate_script(*bad)


REPORT = """noise
SG_POWER_VECTORLESS 0.006 0.004 1.3e-8 0.010137303732335567
SG_POWER_NETS annotated 3274 missing 0 supply 2
SG_POWER_ACTIVITY 0.0046 0.0029 1.4e-8 0.007538514211773872
"""


def test_the_report_is_read_and_checked_against_the_flows_own_number():
    report = power.parse_report(REPORT)
    assert report["vectorless"][3] == pytest.approx(0.010137303732335567)
    assert report["activity"][3] == pytest.approx(0.007538514211773872)
    assert (report["annotated"], report["missing"], report["supply"]) == (3274, 0, 2)
    assert power.check(report, 0.0101373) == (0.007538514211773872, [])
    value, problems = power.check(report, 0.0102)
    assert value is None and "does not match" in problems[0]
    missing = power.parse_report(REPORT.replace("missing 0", "missing 3"))
    value, problems = power.check(missing, 0.0101373)
    assert value is None and "3 nets" in problems[0]
    for text in ("", REPORT.replace("SG_POWER_ACTIVITY", "X"), REPORT.replace("0.007538514211773872", "nan"),
                 REPORT.replace("annotated 3274", "annotated 0")):
        value, problems = power.check(power.parse_report(text), 0.0101373)
        assert value is None and problems


def test_the_power_script_never_asks_a_supply_net_for_its_pins():
    # OpenROAD crashed (signal 11) asking VDD for its drivers: a net missing from the activity file
    # is classified by its signal type in the database before any pin query.
    text = power.POWER_TCL
    loop = text[text.index("foreach sg_net"):]
    assert loop.index("dict exists $sg_act $sg_name") < loop.index("get_pins")
    assert "getSigType" in loop and "{POWER GROUND}" in loop
    assert loop.index("getSigType") < loop.index("get_pins")


def test_a_simulation_past_its_deadline_stops_and_is_never_a_number():
    import time
    cells = {"ff": _cell("$_DFF_P_", C=2, D=10, Q=9), "inv": _cell("$_NOT_", A=9, Y=10)}
    net = power.load_gates(_module(cells, PORTS), "top")
    with pytest.raises(power.PowerError, match="time cap"):
        power.simulate(net, [(0, 0)] * 9, [("a", 2)], deadline=time.monotonic() - 1)
    assert power.simulate(net, [(0, 0)] * 9, [("a", 2)], deadline=time.monotonic() + 60)[1] == 9


def test_the_power_script_has_a_final_and_a_global_route_form():
    final, grt = power.power_tcl("final"), power.power_tcl("grt")
    assert final == power.POWER_TCL
    assert "load_design 6_1_fill.odb 6_1_fill.sdc" in final and "read_spef $::env(RESULTS_DIR)/6_final.spef" in final
    assert "load_design 5_1_grt.odb 5_1_grt.sdc" in grt and "estimate_parasitics -global_routing" in grt
    assert "read_spef" not in grt
    for text in (final, grt):
        assert (text.index('puts "SG_POWER_VECTORLESS') < text.index("set_power_activity")
                < text.index('puts "SG_POWER_ACTIVITY'))
    netlist = power.netlist_tcl()
    assert "load_design 5_1_grt.odb 5_1_grt.sdc" in netlist
    assert "write_verilog $::env(SG_POWER_NETLIST) -remove_cells [find_physical_only_masters]" in netlist
    with pytest.raises(ValueError):
        power.power_tcl("cts")
