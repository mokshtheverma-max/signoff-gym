"""Version 3 of the lint (PLAN.md V3.5 A and B).

A: registers stay where the RTL puts them. The v2 script ran `memory` straight after `proc`,
so Yosys merged an input register into a constant table's read port and the registered-ports
rule refused a design that does register its inputs; version 3 runs `memory -nordff`.
B: every flip-flop clears when rst is high, as the instruction promises: the script holds rst
at 1, folds constants, and every flip-flop's next value must be the constant 0.

The fixtures are the pinned image's own Yosys output (tests/fixtures/f2v3/): rom_add.v, a
registered table read feeding an adder, elaborated with `memory` and with `memory -nordff`;
styles.v, thirteen ways to write (or not write) a reset, elaborated as the v3 lint does and
again with rst held at 1.
"""

import copy
import json
import os

import pytest

from sg.rtl import yosys

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES = os.path.join(REPO, "tests", "fixtures", "f2v3")


def fixture(name):
    with open(os.path.join(FIXTURES, f"{name}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def test_the_lint_script_keeps_registers_out_of_table_reads_and_ties_the_reset_after_the_testbench_netlist():
    lines = yosys.elaborate_script(["rtl_00.v"], "m_top", "design.json", "design.il", "reset.json").splitlines()
    assert "memory -nordff" in lines and "memory" not in lines
    order = [lines.index(line) for line in ("proc", "flatten", "memory -nordff", "check", "write_json design.json",
                                            "write_rtlil design.il", "connect -set rst 1'1",
                                            "write_json reset.json")]
    assert order == sorted(order)
    # The testbench's RTLIL and the lint's JSON come before the reset is tied.
    assert lines.index("write_rtlil design.il") < lines.index("connect -set rst 1'1")
    folding = lines[lines.index("connect -set rst 1'1") + 1:lines.index("write_json reset.json")]
    assert folding == [yosys.FOLD, "opt_muxtree", yosys.FOLD]
    assert not any(line.startswith(("opt_clean", "opt_dff", "opt ", "clean")) for line in folding)


def test_the_lint_script_refuses_names_it_did_not_choose():
    for out in ("reset.json; x", "../reset.json x"):
        with pytest.raises(ValueError):
            yosys.elaborate_script(["rtl_00.v"], "m_top", "design.json", "design.il", out)
    with pytest.raises(ValueError):
        yosys.elaborate_script(["rtl_00.v"], "m_top", "design.json", "design.il", "reset.json", reset="r st")


def test_a_registered_table_read_passes_the_rule_once_the_fold_is_off():
    # The v2 script's netlist: the register merged into the table, the port on its select lines.
    folded = yosys.registered_port_problems(fixture("rom_add_now")["modules"]["m_rom"], "clk")
    assert folded == ["registered ports: input a: 3 of 3 bits reach $mux S before a flip-flop's data input"]
    # The v3 script's netlist: the input register stays where the RTL puts it.
    assert yosys.registered_port_problems(fixture("rom_add_nordff")["modules"]["m_rom"], "clk") == []
    assert yosys.netlist_problems(fixture("rom_add_nordff"), "m_rom", "clk") == []


def _reset_problems(lint=None, reset=None):
    return yosys.reset_problems(lint or fixture("styles_lint"), reset if reset is not None else
                                fixture("styles_reset"), "t")


def test_every_flip_flop_that_does_not_clear_is_named_with_its_bits():
    assert _reset_problems() == [
        "reset: register bad_async1: 8 of 8 bits do not clear when rst is high",
        "reset: register bad_enable_inner: 8 of 8 bits do not clear when rst is high",
        "reset: register bad_none: 8 of 8 bits do not clear when rst is high",
        "reset: register bad_nonzero: 2 of 8 bits do not clear when rst is high",
        "reset: register partial: 4 of 8 bits do not clear when rst is high",
        "reset: register q1: 8 of 8 bits do not clear when rst is high",
    ]


def test_every_way_of_writing_a_reset_that_clears_passes():
    found = " ".join(_reset_problems())
    for name in ("ok_ifrst", "ok_ternary", "ok_and", "ok_enable_outer", "ok_case", "ok_notrst", "ok_async", "q2",
                 "register y:"):
        assert name not in found


def test_a_flip_flop_the_folding_lost_or_changed_never_passes():
    lint, reset = fixture("styles_lint"), fixture("styles_reset")
    cells = reset["modules"]["t"]["cells"]
    lost = next(name for name, cell in sorted(cells.items()) if cell["type"] == "$dff"
                and cell["connections"]["D"] == ["0"] * 8)
    gone = copy.deepcopy(reset)
    del gone["modules"]["t"]["cells"][lost]
    assert len(_reset_problems(lint, gone)) == len(_reset_problems()) + 1
    other = copy.deepcopy(reset)
    other["modules"]["t"]["cells"][lost]["type"] = "$dffe"
    assert len(_reset_problems(lint, other)) == len(_reset_problems()) + 1


def test_a_flip_flop_type_the_rule_cannot_settle_never_passes():
    lint, reset = fixture("styles_lint"), fixture("styles_reset")
    for doc in (lint, reset):
        for cell in doc["modules"]["t"]["cells"].values():
            if cell["type"] == "$dff" and cell["connections"]["Q"]:
                cell["type"] = "$dffsr"
    found = _reset_problems(lint, reset)
    assert any("register ok_ifrst: 8 of 8" in p for p in found)


def test_an_asynchronous_reset_clears_only_to_zero_and_only_on_rst():
    lint, reset = fixture("styles_lint"), fixture("styles_reset")
    cell = next(c for c in reset["modules"]["t"]["cells"].values() if c["type"] == "$adff"
                and c["parameters"]["ARST_VALUE"] == "00000000")
    cell["connections"]["ARST"] = ["0"]
    found = _reset_problems(lint, reset)
    assert any(p.startswith("reset: register ok_async: 8 of 8") for p in found)


def test_a_missing_or_unreadable_reset_netlist_fails_the_rule():
    lint = fixture("styles_lint")
    for broken in (None, {}, {"modules": {}}, {"modules": {"t": []}}):
        assert yosys.reset_problems(lint, broken, "t") == ["reset: the reset check's netlist could not be read"]


def test_an_asynchronous_reset_must_be_the_reset_port_itself():
    # So every design the lint accepts can be simulated for power (PLAN.md V3.5 E 5).
    lint, reset = fixture("styles_lint"), fixture("styles_reset")
    en = lint["modules"]["t"]["ports"]["en"]["bits"]
    cell = next(c for c in lint["modules"]["t"]["cells"].values()
                if c["type"] == "$adff" and c["parameters"]["ARST_VALUE"] == "00000000")
    cell["connections"]["ARST"] = en
    found = _reset_problems(lint, reset)
    assert "reset: register ok_async: an asynchronous reset not driven by rst" in found
    assert not any("ok_async" in p for p in _reset_problems())
