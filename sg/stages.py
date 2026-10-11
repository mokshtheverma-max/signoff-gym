"""The flow, stage by stage, each under a wall-clock and a memory cap (PLAN.md T0.1).

The stages are the ORFS make targets `make all` runs, in its order
(`all: check-yosys check-openroad synth floorplan place cts route finish`),
with global routing split from detailed routing, then KLayout DRC and LVS.
Each stage runs in its own session. A watchdog samples the stage's wall time
and the resident memory of every process in that session twice a second,
and kills the whole session when either passes its cap. A stage stopped by a
cap is reported as such: never as a finished flow, never as a failed design.

This is the verifier's copy (vendored into tests/sg/). The counted runner in
the agent's image has its own, sg/env/stage_guard.py, so no verifier file
sits there; tests/test_stages.py requires both to give the same answers.

over_cap is pure; supervise takes its clock, memory sampler and kill as
arguments, and the /proc readers take the /proc folder, so all of it can be
tested without a real process. Standard library only (the image's Python 3.10).
"""

import math
import os
import signal
import subprocess
import time

STAGES = ("synth", "floorplan", "place", "cts", "grt", "route", "finish", "drc", "lvs")
POLL_S = 0.5
TIME, MEMORY = "time", "memory"
_PAGE_KIB = os.sysconf("SC_PAGE_SIZE") // 1024


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def check_limits(limits):
    """Raise ValueError unless limits is {"seconds": {every stage: whole
    number >= 1}, "memory_mib": whole number >= 1} and nothing else."""
    if not isinstance(limits, dict) or sorted(limits) != ["memory_mib", "seconds"]:
        raise ValueError("stage limits need exactly seconds and memory_mib")
    seconds = limits["seconds"]
    if not isinstance(seconds, dict) or sorted(seconds) != sorted(STAGES):
        raise ValueError(f"stage limits need a wall-clock cap for exactly {', '.join(STAGES)}")
    for stage in STAGES:
        if not _whole(seconds[stage]):
            raise ValueError(f"stage {stage}: the wall-clock cap must be a whole number of seconds >= 1")
    if not _whole(limits["memory_mib"]):
        raise ValueError("the memory cap must be a whole number of MiB >= 1")


def total_seconds(limits):
    """The whole rebuild's cap: the sum of the stage caps."""
    return sum(limits["seconds"][stage] for stage in STAGES)


def limit_text(stage, kind, limits):
    """What result.json says about a stage stopped by a cap. Only the stage
    and the cap: nothing that varies from run to run."""
    if kind == TIME:
        return f"stage {stage} passed its wall-clock cap of {limits['seconds'][stage]} s"
    if kind == MEMORY:
        return f"stage {stage} passed its memory cap of {limits['memory_mib']} MiB"
    raise ValueError(f"unknown cap {kind!r}")


def over_cap(elapsed_s, rss_mib, cap_s, cap_mib):
    """The cap a stage is past, TIME or MEMORY, or None. Exactly at a cap is
    not past it; past both, TIME is named."""
    if elapsed_s > cap_s:
        return TIME
    if rss_mib > cap_mib:
        return MEMORY
    return None


def _read(path):
    with open(path, encoding="ascii", errors="replace") as handle:
        return handle.read()


def _session_of(stat_text):
    """Session id from a /proc/<pid>/stat line. The command name sits in
    parentheses and may itself hold spaces and parentheses, so the fields are
    read after the last ')': state, ppid, pgrp, session."""
    return int(stat_text[stat_text.rindex(")") + 2:].split()[3])


def session_pids(session, proc_root="/proc"):
    """Every process id whose session is `session`, in order."""
    pids = []
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        try:
            if _session_of(_read(os.path.join(proc_root, name, "stat"))) == session:
                pids.append(int(name))
        except (OSError, ValueError, IndexError):
            continue
    return sorted(pids)


def session_rss_kib(session, proc_root="/proc"):
    """Total resident memory, in KiB, of every process in `session`. A
    process that ends while it is read is skipped."""
    total = 0
    for pid in session_pids(session, proc_root):
        try:
            total += int(_read(os.path.join(proc_root, str(pid), "statm")).split()[1]) * _PAGE_KIB
        except (OSError, ValueError, IndexError):
            continue
    return total


def supervise(proc, cap_s, cap_mib, clock, sample, kill, poll_s=POLL_S):
    """Wait for proc, stopping it at a cap. Returns exit_code (None when a
    cap stopped it), limit (None, TIME or MEMORY), seconds and peak_mib (the
    largest memory sample, rounded up; 0 when the stage ended before the
    first sample).

    proc has wait(timeout) raising subprocess.TimeoutExpired; clock gives
    monotonic seconds; sample gives the stage's memory in MiB now; kill stops
    the whole session."""
    start = clock()
    peak = 0.0
    while True:
        try:
            code = proc.wait(timeout=poll_s)
        except subprocess.TimeoutExpired:
            pass
        else:
            return {"exit_code": code, "limit": None, "seconds": round(clock() - start, 1),
                    "peak_mib": math.ceil(peak)}
        rss = sample()
        peak = max(peak, rss)
        limit = over_cap(clock() - start, rss, cap_s, cap_mib)
        if limit:
            kill()
            proc.wait()
            return {"exit_code": None, "limit": limit, "seconds": round(clock() - start, 1),
                    "peak_mib": math.ceil(peak)}


def kill_session(session, proc_root="/proc"):
    """SIGKILL the session's process group, then any member left outside it."""
    try:
        os.killpg(session, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    for pid in session_pids(session, proc_root):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_stage(argv, cap_s, cap_mib, log_path, **popen_args):
    """Run one stage command in its own session under both caps, output to
    log_path. popen_args go to subprocess.Popen (cwd, env, user, group, ...)."""
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, **popen_args)
        return supervise(proc, cap_s, cap_mib, time.monotonic,
                         lambda: session_rss_kib(proc.pid) / 1024.0,
                         lambda: kill_session(proc.pid))
