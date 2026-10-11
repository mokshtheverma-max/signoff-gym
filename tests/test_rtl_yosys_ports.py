"""The fixed Yosys scripts, their log and netlist checks, and the port lock
(PLAN.md F2.5 B and C), the pure parts."""

import pytest

from sg.rtl import ports, yosys

PORTS = [("clk", "input", 1, False), ("rst", "input", 1, False), ("x_ab12", "input", 16, True),
         ("y_cd34", "output", 32, True)]


def test_the_lock_ignores_port_order_and_sees_every_change():
    base = ports.lock("m_top", PORTS)
    assert base == ports.lock("m_top", list(reversed(PORTS)))
    changed = [
        [("clk", "input", 1, False), ("rst", "input", 1, False), ("x_ab12", "input", 17, True), PORTS[3]],
        [("clk", "input", 1, False), ("rst", "input", 1, False), ("x_ab12", "input", 16, False), PORTS[3]],
        [("clk", "input", 1, False), ("rst", "input", 1, False), ("x_ab12", "output", 16, True), PORTS[3]],
        [("clk", "input", 1, False), ("rst", "input", 1, False), ("x_ab13", "input", 16, True), PORTS[3]],
        PORTS[:3],
        PORTS + [("z", "output", 1, False)],
    ]
    for other in changed:
        assert ports.lock("m_top", other) != base
    assert ports.lock("m_other", PORTS) != base


def _reg(d, q):
    return {"type": "$dff", "parameters": {"CLK_POLARITY": "1"},
            "port_directions": {"CLK": "input", "D": "input", "Q": "output"},
            "connections": {"CLK": [2], "D": list(d), "Q": list(q)}}


# Every elaborated F2 design registers its inputs and drives its outputs from
# flip-flops (the registered-ports rule, PLAN.md V2.5 B), so the netlist the
# other structural rules are tested on does too.
REGISTERED = {"in_reg": _reg(range(4, 20), range(60, 76)),
              "out_reg": _reg(list(range(60, 76)) * 2, range(20, 52))}


def _doc(cells=None, extra_modules=(), clock_bits=(2,)):
    module = {"ports": {"clk": {"direction": "input", "bits": list(clock_bits)},
                        "rst": {"direction": "input", "bits": [3]},
                        "x_ab12": {"direction": "input", "signed": 1, "bits": list(range(4, 20))},
                        "y_cd34": {"direction": "output", "signed": 1, "bits": list(range(20, 52))}},
              "cells": {**REGISTERED, **(cells or {})}}
    doc = {"modules": {"m_top": module}}
    for name in extra_modules:
        doc["modules"][name] = {"ports": {}, "cells": {}}
    return doc


def test_ports_come_from_the_elaborated_json():
    got, problems = ports.from_json(_doc(), "m_top")
    assert problems == [] and sorted(got) == sorted(PORTS)
    assert ports.from_json(_doc(), "m_missing")[1]


def _ff(kind="$dff", clk=(2,), polarity="1"):
    return {"type": kind, "parameters": {"CLK_POLARITY": polarity}, "connections": {"CLK": list(clk)}}


def test_a_clean_netlist_has_no_problem():
    cells = {"a": _ff(), "b": {"type": "$add", "parameters": {}, "connections": {}},
             "c": _ff("$sdff"), "d": _ff("$adff", polarity="00000001")}
    assert yosys.netlist_problems(_doc(cells), "m_top", "clk") == []


@pytest.mark.parametrize("cell, word", [
    ({"type": "$dlatch", "parameters": {}, "connections": {}}, "latch"),
    ({"type": "$_DLATCH_P_", "parameters": {}, "connections": {}}, "latch"),
    (_ff(clk=(3,)), "not clocked"),
    (_ff(polarity="0"), "not clocked"),
    ({"type": "$mem_v2", "parameters": {}, "connections": {}}, "memory"),
    ({"type": "sky130_fd_sc_hd__inv_1", "parameters": {}, "connections": {}}, "not a Yosys internal cell"),
    ({"type": "$print", "parameters": {}, "connections": {}}, "not allowed"),
    ({"type": "$assert", "parameters": {}, "connections": {}}, "not allowed"),
])
def test_each_structural_rule_is_enforced(cell, word):
    problems = yosys.netlist_problems(_doc({"bad": cell}), "m_top", "clk")
    assert len(problems) == 1 and word in problems[0]


def test_a_left_over_module_and_a_bad_clock_port_are_problems():
    assert "modules left" in yosys.netlist_problems(_doc(extra_modules=("m_sub",)), "m_top", "clk")[0]
    assert "clock port" in yosys.netlist_problems(_doc(clock_bits=(2, 3)), "m_top", "clk")[0]
    assert "not in the elaborated" in yosys.netlist_problems({"modules": {}}, "m_top", "clk")[0]


def test_log_findings_report_every_warning_and_error():
    text = ("Warning: Latch inferred for signal x\nsome line\nWarning: Identifier `a' is implicitly declared.\n"
            "ERROR: Module `foo' referenced in module `m_top' is not part of the design.\n")
    warnings, errors = yosys.log_findings(text)
    assert len(warnings) == 2 and "Latch" in warnings[0]
    assert len(errors) == 1 and "not part of the design" in errors[0]
    assert yosys.ALLOWED_WARNINGS == ()


def test_the_scripts_read_the_design_as_the_flow_does_and_refuse_other_names():
    script = yosys.elaborate_script(["rtl_00.v", "rtl_01.v"], "m_top", "design.json", "design.il", "reset.json")
    assert script.splitlines()[:2] == ["read_verilog -defer -sv rtl_00.v", "read_verilog -defer -sv rtl_01.v"]
    assert "hierarchy -check -top m_top" in script and "write_json design.json" in script
    for bad in (["top.v"], ["rtl_00.v; shell"], [], ["../rtl_00.v"]):
        with pytest.raises(ValueError):
            yosys.elaborate_script(bad, "m_top", "design.json", "design.il", "reset.json")
    with pytest.raises(ValueError):
        yosys.elaborate_script(["rtl_00.v"], "m top", "design.json", "design.il", "reset.json")
    with pytest.raises(ValueError):
        yosys.elaborate_script(["rtl_00.v"], "m_top", "design.json; x", "design.il", "reset.json")
    sim = yosys.sim_script("design.il", "m_top", "trace.yw", "out.vcd")
    assert "sim -r trace.yw -scope m_top -width 10 -vcd out.vcd m_top" in sim
    eq = yosys.equiv_script(["gold_00.v"], ["rtl_00.v"], "m_top", 5)
    for line in ("equiv_make gold gate equiv", "equiv_simple -seq 5", "equiv_induct -seq 5", "equiv_status"):
        assert line in eq.splitlines()
    assert "equiv_status -assert" not in eq
    with pytest.raises(ValueError):
        yosys.equiv_script(["rtl_00.v"], ["rtl_00.v"], "m_top", 5)


def test_the_equivalence_word_never_calls_a_timeout_proved():
    proven = "Found 800 $equiv cells in equiv:\n  Of those cells 800 are proven and 0 are unproven.\n"
    assert yosys.equiv_verdict(proven, True) == "not proved (time limit)"
    assert yosys.equiv_verdict(proven, False) == "proved"
    open_cells = "Found 32 $equiv cells in equiv:\n  Of those cells 0 are proven and 32 are unproven.\n"
    assert yosys.equiv_verdict(open_cells, False) == "not proved (32 of 32 equivalence points unproven)"
    assert yosys.equiv_verdict("ERROR: Can't open input file `x'\n", False) == "not proved (the check did not run)"
    assert yosys.equiv_verdict("", False) == "not proved (the check did not run)"
    none = "Found 0 $equiv cells in equiv:\n  Of those cells 0 are proven and 0 are unproven.\n"
    assert yosys.equiv_verdict(none, False).startswith("not proved")
