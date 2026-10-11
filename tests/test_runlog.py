"""The verifier's reading of the root-owned run log (PLAN.md R2-A2, R2-A3). Written before the code."""

import json

import pytest

from sg import runlog


def rec(event, **fields):
    return json.dumps(dict(fields, event=event), sort_keys=True)


def text(*lines):
    return "".join(line + "\n" for line in lines)


START1 = rec("start", run=1, knobs={"A": "1"}, knobs_sha256="aa", caller_uid=1000, at="t1")
END1 = rec("end", run=1, exit_codes={"all": 0, "drc": 0, "lvs": 0}, seconds=61.2, at="t2")
START2 = rec("start", run=2, knobs={}, knobs_sha256="bb", caller_uid=1000, at="t3")
END2 = rec("end", run=2, exit_codes={"all": 2}, seconds=10.0, at="t4")


def test_empty_log_is_intact_with_zero_runs():
    intact, summary, problems = runlog.parse("")
    assert intact and problems == []
    assert summary == {"starts": 0, "ends": 0, "refused": 0, "rejected": 0, "runs": [],
                       "previews": 0, "preview_ends": 0, "preview_refused": 0, "preview_runs": []}


def test_two_runs_a_refusal_and_a_rejection():
    log = text(START1, END1, rec("rejected", problems=["line 1: bad line"], knobs_sha256="cc",
                                 caller_uid=1000, at="t"),
               START2, END2, rec("refused", reason="budget: all 2 full flow runs used",
                                 knobs_sha256="dd", caller_uid=1000, at="t"))
    intact, summary, problems = runlog.parse(log)
    assert intact and problems == []
    assert summary["starts"] == 2 and summary["ends"] == 2
    assert summary["refused"] == 1 and summary["rejected"] == 1
    assert summary["runs"] == [
        {"run": 1, "knobs_sha256": "aa", "exit_codes": {"all": 0, "drc": 0, "lvs": 0}},
        {"run": 2, "knobs_sha256": "bb", "exit_codes": {"all": 2}},
    ]


def test_summary_has_no_timestamps_so_results_stay_byte_stable():
    _, first, _ = runlog.parse(text(START1, END1))
    _, second, _ = runlog.parse(text(START1.replace("t1", "t9"), END1.replace("t2", "t8")))
    assert first == second
    assert "at" not in json.dumps(first)


def test_a_run_still_going_at_the_end_is_counted():
    intact, summary, problems = runlog.parse(text(START1, END1, START2))
    assert intact and problems == []
    assert summary["starts"] == 2
    assert summary["runs"][-1] == {"run": 2, "knobs_sha256": "bb", "exit_codes": None}


def test_a_run_that_never_ended_before_the_next_one_is_still_counted():
    intact, summary, _ = runlog.parse(text(START1, START2, END2))
    assert intact
    assert summary["starts"] == 2
    assert summary["runs"][0]["exit_codes"] is None


@pytest.mark.parametrize("log, fragment", [
    (text(START2), "start numbered 2, expected 1"),
    (text(START1, END1, START1), "start numbered 1, expected 2"),
    (text(END1), "end of run 1 without a matching start"),
    (text(START1, END2), "end of run 2 without a matching start"),
    (text(START1, END1, END1), "end of run 1 without a matching start"),
    (text("not json"), "line 1: not JSON"),
    (text("[1, 2]"), "line 1: not a record"),
    (text(rec("paused", run=1)), "line 1: unknown event 'paused'"),
    (text(rec("start", run="1")), "start numbered '1', expected 1"),
    (text(rec("start", run=True)), "start numbered True, expected 1"),
])
def test_a_broken_log_is_not_intact(log, fragment):
    intact, _, problems = runlog.parse(log)
    assert intact is False
    assert any(fragment in problem for problem in problems), problems


def test_blank_lines_are_ignored_but_nothing_else_is():
    intact, summary, _ = runlog.parse("\n" + START1 + "\n\n" + END1 + "\n")
    assert intact and summary["starts"] == 1


def test_read_file_refuses_missing_symlinked_and_huge_logs(tmp_path):
    missing = tmp_path / "flow_runs.jsonl"
    assert runlog.read_file(str(missing)) == (None, "run log missing")
    real = tmp_path / "real.jsonl"
    real.write_text(text(START1, END1))
    missing.symlink_to(real)
    assert runlog.read_file(str(missing)) == (None, "run log is not a regular file")
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"\n" * (runlog.MAX_BYTES + 1))
    assert runlog.read_file(str(big)) == (None, "run log larger than 10 MB")
    assert runlog.read_file(str(real)) == (text(START1, END1), None)


# Previews (PLAN.md HT.5 D): their own records, numbered on their own, never
# counted as full runs.

P1 = rec("preview_start", preview=1, knobs={}, knobs_sha256="p1", caller_uid=1000, at="t5")
P1END = rec("preview_end", preview=1, exit_codes={"synth": 0, "grt": 0}, seconds=90.0, at="t6")
P2 = rec("preview_start", preview=2, knobs={}, knobs_sha256="p2", caller_uid=1000, at="t7")


def test_previews_are_counted_apart_from_full_runs():
    log = text(P1, P1END, START1, END1, P2, rec("preview_refused", reason="budget", knobs_sha256="x",
                                                caller_uid=1000, at="t"),
               rec("rejected", mode="preview", problems=["x"], knobs_sha256="y", caller_uid=1000, at="t"))
    intact, summary, problems = runlog.parse(log)
    assert intact and problems == []
    assert summary["starts"] == 1 and summary["ends"] == 1 and summary["refused"] == 0
    assert summary["previews"] == 2 and summary["preview_ends"] == 1 and summary["preview_refused"] == 1
    assert summary["rejected"] == 1
    assert summary["preview_runs"] == [
        {"preview": 1, "knobs_sha256": "p1", "exit_codes": {"synth": 0, "grt": 0}},
        {"preview": 2, "knobs_sha256": "p2", "exit_codes": None},
    ]
    assert "at" not in json.dumps(summary)


@pytest.mark.parametrize("log, fragment", [
    (text(P2), "preview start numbered 2, expected 1"),
    (text(P1, P1), "preview start numbered 1, expected 2"),
    (text(P1END), "end of preview 1 without a matching start"),
    (text(rec("preview_start", run=1)), "preview start numbered None, expected 1"),
    (text(rec("start", preview=1)), "start numbered None, expected 1"),
    (text(START1, rec("preview_end", preview=1)), "end of preview 1 without a matching start"),
    (text(P1, rec("end", run=1)), "end of run 1 without a matching start"),
])
def test_a_broken_preview_log_is_not_intact(log, fragment):
    intact, _, problems = runlog.parse(log)
    assert intact is False
    assert any(fragment in problem for problem in problems), problems
