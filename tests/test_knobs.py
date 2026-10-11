"""Tests for sg.knobs, the allowed-edits parser (PLAN.md P1.5). Written before the code."""

import os

import pytest

from sg import knobs

SPEC = {
    "CORE_UTILIZATION": {"type": "float", "min": 20, "max": 50, "default": 38},
    "PLACE_DENSITY_LB_ADDON": {"type": "float", "min": 0.0, "max": 0.2, "default": 0.0},
    "TNS_END_PERCENT": {"type": "int", "min": 0, "max": 100, "default": 100},
    "ABC_AREA": {"type": "int", "min": 0, "max": 1, "default": 0},
    "SETUP_SLACK_MARGIN": {"type": "float", "min": -0.1, "max": 0.3, "default": 0},
}


def parse(text):
    return knobs.parse(text.encode("utf-8"), SPEC)


def test_valid_file_with_comments_blanks_and_export():
    ok, values, errors = parse(
        "# my knobs\n\nCORE_UTILIZATION = 30\nexport ABC_AREA=1\n  TNS_END_PERCENT =  50  \n"
    )
    assert errors == []
    assert ok
    assert values == {"ABC_AREA": "1", "CORE_UTILIZATION": "30", "TNS_END_PERCENT": "50"}


def test_empty_file_means_all_defaults():
    assert parse("") == (True, {}, [])
    assert parse("# nothing\n\n") == (True, {}, [])


def test_crlf_line_endings_are_accepted():
    assert parse("CORE_UTILIZATION = 30\r\nABC_AREA = 0\r\n")[0]


@pytest.mark.parametrize("value", ["20", "50", "20.0", "35.5"])
def test_range_boundaries_are_inclusive(value):
    assert parse(f"CORE_UTILIZATION = {value}\n")[0]


@pytest.mark.parametrize("line, fragment", [
    ("CORE_UTILIZATION = 19.99", "out of range"),
    ("CORE_UTILIZATION = 50.01", "out of range"),
    ("PLACE_DENSITY_LB_ADDON = 0.21", "out of range"),
    ("TNS_END_PERCENT = 1.5", "not an integer"),
    ("TNS_END_PERCENT = 010", "not an integer"),
    ("CORE_UTILIZATION = 1e1", "not a number"),
    ("CORE_UTILIZATION = nan", "not a number"),
    ("CORE_UTILIZATION = inf", "not a number"),
    ("CORE_UTILIZATION = +30", "not a number"),
    ("CORE_UTILIZATION = .5", "not a number"),
    ("CORE_UTILIZATION = 30.", "not a number"),
    ("CORE_UTILIZATION = 030", "not a number"),
    ("ABC_AREA = -0", "not an integer"),
    ("SETUP_SLACK_MARGIN = -0.2", "out of range"),
    ("SKIP_DETAILED_ROUTE = 1", "not an allowed knob"),
    ("NUM_CORES = 16", "not an allowed knob"),
    ("core_utilization = 30", "bad line"),
    ("CORE_UTILIZATION := 30", "bad line"),
    ("CORE_UTILIZATION ?= 30", "bad line"),
    ("CORE_UTILIZATION += 30", "bad line"),
    ("CORE_UTILIZATION = 30 40", "bad line"),
    ("CORE_UTILIZATION = 30 # thirty", "bad line"),
    ("CORE_UTILIZATION = $(shell id)", "bad line"),
    ("CORE_UTILIZATION = `id`", "bad line"),
    ("include /etc/passwd", "bad line"),
    ("CORE_UTILIZATION =", "bad line"),
])
def test_bad_lines_are_refused(line, fragment):
    ok, values, errors = parse(line + "\n")
    assert not ok
    assert values == {}
    assert len(errors) == 1 and fragment in errors[0], errors


def test_negative_value_allowed_only_when_range_allows_it():
    assert parse("SETUP_SLACK_MARGIN = -0.1\n")[0]


def test_duplicate_key_is_refused_not_first_or_last_wins():
    ok, values, errors = parse("ABC_AREA = 0\nABC_AREA = 1\n")
    assert not ok
    assert values == {}
    assert errors == ["line 2: ABC_AREA set twice"]


def test_errors_carry_line_numbers_and_all_are_reported():
    ok, _, errors = parse("ABC_AREA = 3\n\nFOO = 1\n")
    assert not ok
    assert errors == ["line 1: ABC_AREA = 3 out of range [0, 1]",
                      "line 3: FOO is not an allowed knob"]


def test_too_big_file_is_refused():
    ok, _, errors = knobs.parse(b"# " + b"x" * knobs.MAX_BYTES, SPEC)
    assert not ok
    assert errors == [f"file larger than {knobs.MAX_BYTES} bytes"]


def test_nul_and_non_utf8_are_refused():
    assert knobs.parse(b"ABC_AREA = 1\x00\n", SPEC)[2] == ["file contains a NUL byte"]
    assert knobs.parse(b"ABC_AREA = \xff\n", SPEC)[2] == ["file is not UTF-8"]


def test_sanitized_makefile_is_sorted_and_round_trips():
    ok, values, _ = parse("TNS_END_PERCENT = 50\nABC_AREA = 1\n")
    text = knobs.sanitized_mk(values)
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert lines == ["export ABC_AREA = 1", "export TNS_END_PERCENT = 50"]
    assert parse(text) == (True, values, [])


def test_read_file_refuses_symlinks_and_missing(tmp_path):
    real = tmp_path / "real.mk"
    real.write_text("ABC_AREA = 1\n")
    link = tmp_path / "link.mk"
    os.symlink(real, link)
    assert knobs.read_file(str(real)) == (b"ABC_AREA = 1\n", None)
    assert knobs.read_file(str(link)) == (None, "knobs file is a symlink")
    assert knobs.read_file(str(tmp_path / "none.mk")) == (None, "knobs file missing")
    assert knobs.read_file(str(tmp_path)) == (None, "knobs file is not a regular file")


def test_read_file_refuses_oversized_file(tmp_path):
    big = tmp_path / "big.mk"
    big.write_bytes(b"#" * (knobs.MAX_BYTES + 1))
    assert knobs.read_file(str(big)) == (None, f"knobs file larger than {knobs.MAX_BYTES} bytes")


@pytest.mark.parametrize("bad_spec", [
    {"X": {"type": "str", "min": 0, "max": 1, "default": 0}},
    {"X": {"type": "int", "min": 2, "max": 1, "default": 1}},
    {"X": {"type": "int", "min": 0, "max": 1}},
    {"X": {"type": "int", "min": 0, "max": 1, "default": 5}},
    {"lower": {"type": "int", "min": 0, "max": 1, "default": 0}},
])
def test_bad_spec_is_refused(bad_spec):
    with pytest.raises(ValueError):
        knobs.parse(b"", bad_spec)


def test_spec_default_may_be_null_for_knobs_orfs_leaves_unset():
    spec = {"CTS_CLUSTER_SIZE": {"type": "int", "min": 10, "max": 200, "default": None}}
    assert knobs.parse(b"CTS_CLUSTER_SIZE = 30\n", spec) == (True, {"CTS_CLUSTER_SIZE": "30"}, [])


# Allowed edits are plain data only (PLAN.md T0.2): a knobs file that carries
# an injected command in any form is refused whole, by the grader's parser
# and by the counted runner's checker alike, and nothing of it reaches make.

import importlib.util as _importlib_util

_CHECK_PATH = os.path.join(os.path.dirname(__file__), "..", "sg", "env", "check_knobs.py")
_spec = _importlib_util.spec_from_file_location("check_knobs_for_injection", _CHECK_PATH)
check_knobs = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(check_knobs)

INJECTIONS = [
    "$(shell touch /tmp/pwned)",
    "CORE_UTILIZATION = $(shell id -u)",
    "$(eval CORE_UTILIZATION := 20)",
    "CORE_UTILIZATION = `touch /tmp/pwned`",
    "CORE_UTILIZATION = 40; touch /tmp/pwned",
    "CORE_UTILIZATION = 40 && touch /tmp/pwned",
    "CORE_UTILIZATION = 40 | tee /tmp/pwned",
    "CORE_UTILIZATION = 40 \\",
    "CORE_UTILIZATION = 40\r$(shell id)",
    "include /tmp/evil.mk",
    "-include /tmp/evil.mk",
    "override CORE_UTILIZATION = 40",
    "define CORE_UTILIZATION",
    "exec touch /tmp/pwned",
    "set ::env(CORE_UTILIZATION) 40",
    "CORE_UTILIZATION = [exec id]",
    "create_clock -name clk -period 100 [get_ports clk]",
    "set_input_delay 0 -clock clk [all_inputs]",
    "SDC_FILE = /tmp/loose.sdc",
    "export SDC_FILE=/tmp/loose.sdc",
    "CORE_UTILIZATION = " + chr(0xFF14) + chr(0xFF10),
    "CORE_UTILIZATION = " + chr(0x0664) + chr(0x0660),
    "CORE_UTILIZATION = 4e1",
    "CORE_UTILIZATION = 0x28",
    "CORE_UTILIZATION = 40.0.0",
]


@pytest.mark.parametrize("line", INJECTIONS)
def test_a_knobs_file_with_an_injected_command_is_refused_by_both_checkers(line):
    data = f"TNS_END_PERCENT = 50\n{line}\n".encode("utf-8")
    ok, values, errors = knobs.parse(data, SPEC)
    settings, problems = check_knobs.check(data, SPEC)
    assert not ok and values == {} and len(errors) == 1 and errors[0].startswith("line 2: ")
    assert settings == {} and problems == errors


def test_the_flow_only_ever_sees_checked_keys_and_plain_numbers():
    ok, values, _ = parse("TNS_END_PERCENT = 50\nexport CORE_UTILIZATION=30\n")
    assert ok
    lines = knobs.sanitized_mk(values).splitlines()
    assert lines[0].startswith("# ")
    for line in lines[1:]:
        key, value = line.removeprefix("export ").split(" = ")
        assert key in SPEC
        assert value.replace(".", "", 1).isdigit() and value.isascii()
