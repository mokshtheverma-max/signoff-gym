"""Per-stage wall-clock and memory caps (PLAN.md T0.1). Written before the code.

Two watchdogs, one behaviour: sg/stages.py runs the verifier's rebuild and
sg/env/stage_guard.py runs the agent's counted flow runs (no verifier file
sits in the agent image). Every case here runs on both and must give the
same answer. The test sandbox forbids subprocesses, so the supervision loop
is driven by a fake clock, a fake process and fake memory samples, and
/proc is a folder in the temp directory; the real kill path is exercised by
the ship check's stall lines.
"""

import importlib.util
import os
import subprocess

import pytest

from sg import pins as pins_mod
from sg import stages

GUARD_PATH = os.path.join(pins_mod.REPO, "sg", "env", "stage_guard.py")


def _guard():
    spec = importlib.util.spec_from_file_location("stage_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _guard()
BOTH = [pytest.param(stages, id="verifier"), pytest.param(guard, id="counted-runner")]
PAGE_KIB = os.sysconf("SC_PAGE_SIZE") // 1024
ALL = ("synth", "floorplan", "place", "cts", "grt", "route", "finish", "drc", "lvs")


def limits(**seconds):
    table = {stage: 300 for stage in ALL}
    table.update(seconds)
    return {"seconds": table, "memory_mib": 4096}


# The stages


def test_stages_are_make_all_split_at_global_route_then_signoff():
    # ORFS: all: check-yosys check-openroad synth floorplan place cts route finish
    assert stages.STAGES == ALL
    assert tuple(guard.STAGES) == ALL


# The decision


@pytest.mark.parametrize("module", BOTH)
@pytest.mark.parametrize("elapsed, rss, expected", [
    (0.0, 0.0, None),
    (9.5, 999.0, None),
    (10.0, 1000.0, None),          # exactly at a cap is not past it
    (10.5, 0.0, "time"),
    (0.5, 1000.5, "memory"),
    (10.5, 5000.0, "time"),        # both past: time is named first
])
def test_over_cap(module, elapsed, rss, expected):
    assert module.over_cap(elapsed, rss, 10, 1000) == expected


# The supervision loop


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeProc:
    """A process that ends at ends_at (never when None), with exit code code."""

    def __init__(self, clock, ends_at=None, code=0):
        self.clock, self.ends_at, self.code = clock, ends_at, code
        self.killed = False

    def wait(self, timeout=None):
        if self.killed:
            return -9
        if self.ends_at is not None and (timeout is None or self.clock.now + timeout >= self.ends_at):
            self.clock.now = max(self.clock.now, self.ends_at)
            return self.code
        if timeout is None:
            raise AssertionError("a blocking wait on a process that never ends")
        if self.clock.now > 3600:
            # A watchdog whose cap never fires would wait forever; fail instead.
            raise AssertionError("the watchdog never stopped a stage that runs forever")
        self.clock.now += timeout
        raise subprocess.TimeoutExpired("fake", timeout)


def run_case(module, ends_at, code, samples, cap_s, cap_mib):
    clock = FakeClock()
    proc = FakeProc(clock, ends_at, code)
    kills = []
    queue = list(samples)

    def sample():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def kill():
        kills.append(clock.now)
        proc.killed = True

    result = module.supervise(proc, cap_s, cap_mib, clock, sample, kill, poll_s=0.5)
    return result, kills


CASES = [
    # ends_at, code, samples (MiB), cap_s, cap_mib, expected result, kills
    (3.2, 0, [100.0, 150.0, 120.0], 10, 1000,
     {"exit_code": 0, "limit": None, "seconds": 3.2, "peak_mib": 150}, 0),
    (1.0, 2, [10.0], 10, 1000,
     {"exit_code": 2, "limit": None, "seconds": 1.0, "peak_mib": 10}, 0),
    (None, 0, [10.0], 5, 1000,
     {"exit_code": None, "limit": "time", "seconds": 5.5, "peak_mib": 10}, 1),
    (None, 0, [100.0, 200.0, 2000.25], 60, 1000,
     {"exit_code": None, "limit": "memory", "seconds": 1.5, "peak_mib": 2001}, 1),
    (5.0, 0, [10.0], 5, 1000,
     {"exit_code": 0, "limit": None, "seconds": 5.0, "peak_mib": 10}, 0),
    (0.2, 0, [99999.0], 5, 1000,
     {"exit_code": 0, "limit": None, "seconds": 0.2, "peak_mib": 0}, 0),
]


@pytest.mark.parametrize("module", BOTH)
@pytest.mark.parametrize("ends_at, code, samples, cap_s, cap_mib, expected, kill_count", CASES)
def test_supervise(module, ends_at, code, samples, cap_s, cap_mib, expected, kill_count):
    result, kills = run_case(module, ends_at, code, samples, cap_s, cap_mib)
    assert result == expected
    assert len(kills) == kill_count


@pytest.mark.parametrize("case", CASES)
def test_both_watchdogs_give_the_same_answer(case):
    ends_at, code, samples, cap_s, cap_mib, _, _ = case
    assert run_case(stages, ends_at, code, samples, cap_s, cap_mib) == \
        run_case(guard, ends_at, code, samples, cap_s, cap_mib)


# Reading /proc


def fake_proc(tmp_path):
    root = tmp_path / "proc"
    entries = {
        "1": ("1 (init) S 0 1 1 0 -1 4194560", "100 50 0 0 0 0 0"),
        "100": ("100 (bash) S 1 100 100 0 -1 4194560", "1000 300 10 1 0 50 0"),
        "101": ("101 (make (inner) x) R 100 100 100 0 -1 4194560", "9000 5000 10 1 0 50 0"),
        "102": ("102 (openroad) R 101 102 100 0 -1 4194560", "80000 60000 10 1 0 50 0"),
        "200": ("200 (other) S 1 200 200 0 -1 4194560", "5 5 0 0 0 0 0"),
        "300": ("300 (gone) S 100 100 100 0 -1 4194560", None),
    }
    for pid, (stat, statm) in entries.items():
        folder = root / pid
        folder.mkdir(parents=True)
        (folder / "stat").write_text(stat + "\n")
        if statm is not None:
            (folder / "statm").write_text(statm + "\n")
    (root / "self").mkdir()
    (root / "400").mkdir()
    return str(root)


@pytest.mark.parametrize("module", BOTH)
def test_session_members_are_found_whatever_the_command_name(module, tmp_path):
    root = fake_proc(tmp_path)
    assert module.session_pids(100, root) == [100, 101, 102, 300]
    assert module.session_pids(200, root) == [200]
    assert module.session_pids(999, root) == []


@pytest.mark.parametrize("module", BOTH)
def test_session_memory_is_the_resident_memory_of_every_member(module, tmp_path):
    root = fake_proc(tmp_path)
    assert module.session_rss_kib(100, root) == (300 + 5000 + 60000) * PAGE_KIB
    assert module.session_rss_kib(999, root) == 0


# Caps from the task


def test_limits_need_every_stage_a_whole_number_of_seconds_and_one_memory_cap():
    stages.check_limits(limits())
    stages.check_limits(limits(route=1800))


@pytest.mark.parametrize("bad", [
    None, [], {"seconds": {}, "memory_mib": 4096},
    {"seconds": {s: 300 for s in ALL if s != "lvs"}, "memory_mib": 4096},
    {"seconds": dict({s: 300 for s in ALL}, extra=10), "memory_mib": 4096},
    {"seconds": dict({s: 300 for s in ALL}, synth=0), "memory_mib": 4096},
    {"seconds": dict({s: 300 for s in ALL}, synth=1.5), "memory_mib": 4096},
    {"seconds": dict({s: 300 for s in ALL}, synth=True), "memory_mib": 4096},
    {"seconds": {s: 300 for s in ALL}},
    {"seconds": {s: 300 for s in ALL}, "memory_mib": 0},
    {"seconds": {s: 300 for s in ALL}, "memory_mib": 4096.0},
    {"seconds": {s: 300 for s in ALL}, "memory_mib": 4096, "extra": 1},
])
def test_bad_limits_are_refused(bad):
    with pytest.raises(ValueError):
        stages.check_limits(bad)


def test_total_is_the_sum_of_the_stage_caps():
    assert stages.total_seconds(limits(route=1800)) == 8 * 300 + 1800


def test_limit_text_names_the_stage_and_the_cap_and_nothing_that_varies():
    table = limits(route=1800)
    table["memory_mib"] = 64
    assert stages.limit_text("route", "time", table) == "stage route passed its wall-clock cap of 1800 s"
    assert stages.limit_text("synth", "memory", table) == "stage synth passed its memory cap of 64 MiB"
    with pytest.raises(ValueError):
        stages.limit_text("synth", "disk", table)


# The real process path, statically (it is run for real by the ship check)


@pytest.mark.parametrize("path", [os.path.join(pins_mod.REPO, "sg", "stages.py"), GUARD_PATH])
def test_a_stage_runs_in_its_own_session_and_is_killed_whole(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "start_new_session=True" in source
    assert "stdin=subprocess.DEVNULL" in source
    assert "os.killpg(" in source and "signal.SIGKILL" in source
    assert "time.monotonic" in source
