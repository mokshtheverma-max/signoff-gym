"""The registered-ports rule checked again on the finished netlist (PLAN.md P.6 4 Oct 19:51, version 4).
Netlists here have the shape Yosys writes for 6_final.v read with the kit's cells as models and
flattened (sg/rtl/power.py gate_script): Yosys's own gate and flip-flop cells. Written before the code."""

from sg.rtl import netlist_ports

TOP = "top"


def net(ports, cells):
    """ports: {name: (direction, [bits])}; cells: {name: (type, {pin: [bits]})}."""
    return {"modules": {TOP: {
        "ports": {name: {"direction": d, "bits": bits} for name, (d, bits) in ports.items()},
        "cells": {name: {"type": kind, "connections": conn} for name, (kind, conn) in cells.items()}}}}


# clk 2, rst 3, input a bits 10 and 11, output y bits 20 and 21
PORTS = {"clk": ("input", [2]), "rst": ("input", [3]), "a": ("input", [10, 11]), "y": ("output", [20, 21])}


def clean_cells():
    return {
        "in0": ("$_DFF_P_", {"C": [2], "D": [10], "Q": [30]}),
        "inv": ("$_NOT_", {"A": [11], "Y": [40]}),
        "buf": ("$_BUF_", {"A": [40], "Y": [41]}),
        "in1": ("$_DFF_P_", {"C": [2], "D": [41], "Q": [31]}),
        "mid": ("$_XOR_", {"A": [30], "B": [31], "Y": [32]}),
        "out0": ("$_DFF_P_", {"C": [2], "D": [32], "Q": [20]}),
        "out1": ("$_DFF_P_", {"C": [2], "D": [30], "Q": [50]}),
        "obuf": ("$_BUF_", {"A": [50], "Y": [21]}),
    }


def test_inputs_straight_into_flip_flops_and_outputs_straight_from_them_pass():
    problems, counts = netlist_ports.check(net(PORTS, clean_cells()), TOP)
    assert problems == []
    assert counts == {"inputs_checked": 2, "outputs_checked": 2}


def test_one_reset_gate_on_the_way_in_is_allowed_and_a_second_is_not():
    cells = clean_cells()
    cells["nrst"] = ("$_NOT_", {"A": [3], "Y": [60]})
    cells["rst_and"] = ("$_AND_", {"A": [10], "B": [60], "Y": [61]})
    cells["in0"] = ("$_DFF_P_", {"C": [2], "D": [61], "Q": [30]})
    assert netlist_ports.check(net(PORTS, cells), TOP)[0] == []
    cells["rst_or"] = ("$_ORNOT_", {"A": [61], "B": [60], "Y": [62]})
    cells["in0"] = ("$_DFF_P_", {"C": [2], "D": [62], "Q": [30]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert len(problems) == 1 and problems[0].startswith("netlist ports: input a: 1 of 2 bits")


def test_logic_on_an_input_is_refused():
    cells = clean_cells()
    cells["add"] = ("$_AND_", {"A": [10], "B": [11], "Y": [70]})
    cells["in0"] = ("$_DFF_P_", {"C": [2], "D": [70], "Q": [30]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems == ["netlist ports: input a: 2 of 2 bits read by logic ($_AND_) before a flip-flop"]


def test_an_xor_with_the_reset_is_logic_not_a_reset():
    cells = clean_cells()
    cells["x"] = ("$_XOR_", {"A": [10], "B": [3], "Y": [71]})
    cells["in0"] = ("$_DFF_P_", {"C": [2], "D": [71], "Q": [30]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems and "read by logic ($_XOR_)" in problems[0]


def test_an_input_that_reaches_no_flip_flop_or_drives_a_flip_flops_other_pins_is_refused():
    cells = clean_cells()
    del cells["in0"]
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems and "reaches no flip-flop" in problems[0]
    cells = clean_cells()
    cells["en"] = ("$_DFFE_PP_", {"C": [2], "D": [32], "E": [10], "Q": [80]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems and "reaches a flip-flop's clock, reset or enable pin" in problems[0]


def test_an_input_wired_to_an_output_is_refused_on_both_sides():
    cells = clean_cells()
    del cells["obuf"]
    cells["thru"] = ("$_BUF_", {"A": [10], "Y": [21]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert any(p.startswith("netlist ports: input a: 1 of 2 bits") and "drives an output port" in p for p in problems)
    assert any(p.startswith("netlist ports: output y: 1 of 2 bits") and "an input port" in p for p in problems)


def test_an_output_from_logic_a_constant_or_nothing_is_refused():
    for driver in (("$_OR_", {"A": [30], "B": [31], "Y": [21]}), None, "const"):
        cells = clean_cells()
        del cells["obuf"]
        ports = dict(PORTS)
        if driver == "const":
            ports["y"] = ("output", [20, "0"])
        elif driver is not None:
            cells["logic"] = driver
        problems = netlist_ports.check(net(ports, cells), TOP)[0]
        assert len(problems) == 1 and problems[0].startswith("netlist ports: output y: 1 of 2 bits not driven"), driver


def test_a_cell_it_does_not_know_is_never_passed():
    cells = clean_cells()
    cells["box"] = ("sky130_fd_sc_hd__dlclkp_1", {"GATE": [10], "CLK": [2], "GCLK": [90]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems and "sky130_fd_sc_hd__dlclkp_1" in problems[0]
    cells = clean_cells()
    del cells["obuf"]
    cells["box"] = ("sky130_fd_sc_hd__buf_8", {"A": [50], "X": [21]})
    problems = netlist_ports.check(net(PORTS, cells), TOP)[0]
    assert problems and problems[0].startswith("netlist ports: output y")


def test_the_clock_and_the_reset_are_not_walked_and_a_missing_top_is_a_problem():
    ports = dict(PORTS, rst=("input", [3]))
    cells = clean_cells()
    cells["rst_use"] = ("$_AND_", {"A": [3], "B": [30], "Y": [91]})
    assert netlist_ports.check(net(ports, cells), TOP)[0] == []
    problems, counts = netlist_ports.check({"modules": {}}, TOP)
    assert problems == ["netlist ports: the top module top is not in the netlist"] and counts is None
