"""KLayout DRC and LVS results as the grader reads them (PLAN.md R2-A1). Written before the code.

Fixtures: 6_drc_clean.lyrdb and 6_lvs_match.log come from the stock riscv32i
run of 24 Sep 2026 (runs/r2-stock-riscv32i); 6_drc_two.lyrdb and
6_lvs_mismatch.log are the same files with two violations added and the LVS
verdict line swapped for the deck's failure line.
"""

import os
import shutil

import pytest

from sg import metrics

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "signoff")


def place(tmp_path, drc=None, lvs=None, drc_text=None, lvs_text=None):
    reports, logs = tmp_path / "reports", tmp_path / "logs"
    reports.mkdir()
    logs.mkdir()
    if drc:
        shutil.copy(os.path.join(FIX, drc), reports / "6_drc.lyrdb")
    if drc_text is not None:
        (reports / "6_drc.lyrdb").write_text(drc_text, encoding="utf-8")
    if lvs:
        shutil.copy(os.path.join(FIX, lvs), logs / "6_lvs.log")
    if lvs_text is not None:
        (logs / "6_lvs.log").write_text(lvs_text, encoding="utf-8")
    return str(reports), str(logs)


def test_clean_run_reads_zero_and_match(tmp_path):
    values, details, problems = metrics.read_signoff(*place(tmp_path, "6_drc_clean.lyrdb", "6_lvs_match.log"))
    assert problems == []
    assert values == {"klayout_drc_count": 0, "lvs_match": True}
    assert details["klayout_drc_by_rule"] == {}


def test_violations_are_counted_per_item_and_per_rule(tmp_path):
    values, details, problems = metrics.read_signoff(*place(tmp_path, "6_drc_two.lyrdb", "6_lvs_match.log"))
    assert problems == []
    assert values["klayout_drc_count"] == 2
    assert details["klayout_drc_by_rule"] == {"li.3": 1, "m1.2": 1}


def test_lvs_mismatch_is_false_not_a_problem(tmp_path):
    values, _, problems = metrics.read_signoff(*place(tmp_path, "6_drc_clean.lyrdb", "6_lvs_mismatch.log"))
    assert problems == []
    assert values["lvs_match"] is False


def test_missing_files_are_problems_never_zero_or_true(tmp_path):
    values, _, problems = metrics.read_signoff(*place(tmp_path))
    assert values == {}
    assert problems == ["6_drc.lyrdb: missing", "6_lvs.log: missing"]


@pytest.mark.parametrize("text, problem", [
    ("<report-database><items>", "6_drc.lyrdb: not valid XML"),
    ("<other><items></items></other>", "6_drc.lyrdb: not a KLayout report database"),
    ("<report-database></report-database>", "6_drc.lyrdb: no items section"),
    ("<report-database><items></items><items></items></report-database>",
     "6_drc.lyrdb: more than one items section"),
])
def test_bad_drc_report_is_a_problem(tmp_path, text, problem):
    values, _, problems = metrics.read_signoff(*place(tmp_path, drc_text=text, lvs="6_lvs_match.log"))
    assert "klayout_drc_count" not in values
    assert problems == [problem]


@pytest.mark.parametrize("text, problem", [
    ("Writing LVS database\n", "6_lvs.log: no LVS verdict"),
    ("INFO : Congratulations! Netlists match.\nINFO : Congratulations! Netlists match.\n",
     "6_lvs.log: LVS verdict reported more than once"),
    ("ERROR : Netlists don't match\nINFO : Congratulations! Netlists match.\n",
     "6_lvs.log: LVS verdict reported more than once"),
])
def test_unclear_lvs_log_is_a_problem(tmp_path, text, problem):
    values, _, problems = metrics.read_signoff(*place(tmp_path, drc="6_drc_clean.lyrdb", lvs_text=text))
    assert "lvs_match" not in values
    assert problems == [problem]


def test_lvs_verdict_must_be_its_own_line(tmp_path):
    # A cell or net name that merely contains the words is not a verdict.
    text = "Net 'INFO : Congratulations! Netlists match. fake'\nERROR : Netlists don't match\n"
    values, _, problems = metrics.read_signoff(*place(tmp_path, drc="6_drc_clean.lyrdb", lvs_text=text))
    assert problems == []
    assert values["lvs_match"] is False
