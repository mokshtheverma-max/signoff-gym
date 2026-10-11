#!/usr/bin/env python3
"""Stage caps for the counted flow runner (PLAN.md T0.1).

flow_runner.py runs each flow stage through run_stage(): the stage gets its
own session, and a watchdog samples its wall time and the resident memory of
every process in that session twice a second. Past either cap, the whole
session is killed and the run stops, recorded as stopped by that cap. The
verifier's rebuild applies the same caps with its own watchdog
(sg/stages.py); a unit test runs both on the same cases and requires the same
answers, so the agent's counted runs stop exactly where the grader's would.
"""

import math
import os
import signal
import subprocess
import time

STAGES = ["synth", "floorplan", "place", "cts", "grt", "route", "finish", "drc", "lvs"]
POLL_SECONDS = 0.5
PAGE_KIB = os.sysconf("SC_PAGE_SIZE") // 1024


def over_cap(elapsed, rss_mib, cap_seconds, cap_mib):
    """"time", "memory" or None. At a cap is not past it; time is named first."""
    if elapsed > cap_seconds:
        return "time"
    return "memory" if rss_mib > cap_mib else None


def session_pids(session, proc_root="/proc"):
    found = []
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "stat"), encoding="ascii", errors="replace") as handle:
                line = handle.read()
            # Fields after the command name, which may hold ')': state ppid pgrp session.
            if int(line[line.rindex(")") + 2:].split()[3]) == session:
                found.append(int(entry))
        except (OSError, ValueError, IndexError):
            pass
    found.sort()
    return found


def session_rss_kib(session, proc_root="/proc"):
    kib = 0
    for pid in session_pids(session, proc_root):
        try:
            with open(os.path.join(proc_root, str(pid), "statm"), encoding="ascii") as handle:
                kib += int(handle.read().split()[1]) * PAGE_KIB
        except (OSError, ValueError, IndexError):
            pass
    return kib


def supervise(proc, cap_seconds, cap_mib, clock, sample, kill, poll_s=POLL_SECONDS):
    """Wait for proc under both caps; see sg/stages.py supervise."""
    began = clock()
    peak = 0.0
    while True:
        try:
            code = proc.wait(timeout=poll_s)
            return {"exit_code": code, "limit": None, "seconds": round(clock() - began, 1),
                    "peak_mib": math.ceil(peak)}
        except subprocess.TimeoutExpired:
            pass
        rss = sample()
        if rss > peak:
            peak = rss
        reason = over_cap(clock() - began, rss, cap_seconds, cap_mib)
        if reason is not None:
            kill()
            proc.wait()
            return {"exit_code": None, "limit": reason, "seconds": round(clock() - began, 1),
                    "peak_mib": math.ceil(peak)}


def kill_session(session, proc_root="/proc"):
    try:
        os.killpg(session, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    for pid in session_pids(session, proc_root):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_stage(command, cap_seconds, cap_mib, log_path, **popen_args):
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, **popen_args)
        return supervise(proc, cap_seconds, cap_mib, time.monotonic,
                         lambda: session_rss_kib(proc.pid) / 1024.0,
                         lambda: kill_session(proc.pid))
