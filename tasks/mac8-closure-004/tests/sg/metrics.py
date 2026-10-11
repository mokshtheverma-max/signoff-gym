"""Read the flow's own metrics (PLAN.md P1.1).

Only two ORFS files are read, by explicit name: the detailed-route stage and
the final report. Nothing is globbed. A missing file or bad JSON becomes a
problem string; a missing key is simply left out. Nothing is ever turned
into a zero: the grader decides what a missing value means.

The clock period and the unconstrained-endpoint count are not in any ORFS
metrics file. They come from OpenSTA run on the flow's final database with
the pristine SDC (sg_checks.tcl), whose output parse_checks reads.

KLayout sign-off (PLAN.md R2-A1) is read by read_signoff: the DRC report
database 6_drc.lyrdb (one <item> per violation) and the LVS log 6_lvs.log
(the sky130hd deck prints one verdict line and exits 0 either way).
"""

import json
import os
import re
import xml.etree.ElementTree as ElementTree

FILES = ("5_2_route.json", "6_report.json")

# Grader name -> (file, ORFS key). Units: ns, W, um^2, Hz.
ORFS_KEYS = {
    "setup_ws_ns": ("6_report.json", "finish__timing__setup__ws"),
    "setup_tns_ns": ("6_report.json", "finish__timing__setup__tns"),
    "hold_ws_ns": ("6_report.json", "finish__timing__hold__ws"),
    "hold_tns_ns": ("6_report.json", "finish__timing__hold__tns"),
    "setup_violation_count": ("6_report.json", "finish__timing__drv__setup_violation_count"),
    "hold_violation_count": ("6_report.json", "finish__timing__drv__hold_violation_count"),
    "route_drc_count": ("5_2_route.json", "detailedroute__route__drc_errors"),
    "antenna_nets": ("5_2_route.json", "detailedroute__antenna__violating__nets"),
    "antenna_pins": ("5_2_route.json", "detailedroute__antenna__violating__pins"),
    "power_w": ("6_report.json", "finish__power__total"),
    "area_um2": ("6_report.json", "finish__design__instance__area__stdcell"),
    "utilization": ("6_report.json", "finish__design__instance__utilization"),
    "die_area_um2": ("6_report.json", "finish__design__die__area"),
    "core_area_um2": ("6_report.json", "finish__design__core__area"),
    "fmax_hz": ("6_report.json", "finish__timing__fmax"),
    "instance_count": ("6_report.json", "finish__design__instance__count__stdcell"),
    "flow_error_count": ("6_report.json", "finish__flow__errors__count"),
    "route_wirelength_um": ("5_2_route.json", "detailedroute__route__wirelength"),
    # The kit's electrical limits (grader version 7, PLAN.md SB.5 A, probe P6 in
    # docs/GRADER.md): OpenSTA's violation counts on the final design, written
    # once by report_erc_metrics after the last repair. Next to them, whatever
    # their names say, the "_limit" keys for slew and capacitance hold the worst
    # pin's slack divided by its limit, and the fanout one holds the limit itself
    # (0 when the kit defines none). Only the three counts are graded.
    "max_slew_violations": ("6_report.json", "finish__timing__drv__max_slew"),
    "max_cap_violations": ("6_report.json", "finish__timing__drv__max_cap"),
    "max_fanout_violations": ("6_report.json", "finish__timing__drv__max_fanout"),
    "max_slew_slack_ratio": ("6_report.json", "finish__timing__drv__max_slew_limit"),
    "max_cap_slack_ratio": ("6_report.json", "finish__timing__drv__max_cap_limit"),
    "max_fanout_limit": ("6_report.json", "finish__timing__drv__max_fanout_limit"),
}

_UNCON_HEADER = re.compile(r"^Warning: There (?:are|is) (\d+) unconstrained endpoints?\.$")


# ORFS logs some keys more than once in one file. For these the grader takes
# the last occurrence on purpose. detail_route.tcl runs check_antennas inside
# the antenna-repair loop and then once more on the final design; it also
# runs detailed_route again after each antenna repair, and the last call is
# the routed design saved as 5_2_route.odb (PLAN.md L.6, R2-G1).
# Any other graded key that appears more than once is a reading problem.
LAST_OCCURRENCE_KEYS = {
    "detailedroute__antenna__violating__nets": "final check_antennas in detail_route.tcl",
    "detailedroute__antenna__violating__pins": "final check_antennas in detail_route.tcl",
    "detailedroute__route__drc_errors": "last detailed_route call in detail_route.tcl",
    "detailedroute__route__wirelength": "last detailed_route call in detail_route.tcl",
}


class _Pairs(list):
    """Marks a parsed JSON object as its list of (key, value) pairs."""


def _load(path):
    """Return (list of [key, value] pairs or None, problem or None).

    Pairs keep every occurrence in file order: Python's json module would
    otherwise keep the last value of a repeated key silently."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        return None, f"{name}: missing"
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_Pairs)
    except (ValueError, UnicodeDecodeError):
        return None, f"{name}: not valid JSON"
    if not isinstance(data, _Pairs):
        return None, f"{name}: not a JSON object"
    return [[key, value] for key, value in data], None


def read_raw(log_dir):
    """Return ({file name: [[key, value], ...] in file order}, problems)."""
    raw, problems = {}, []
    for name in FILES:
        data, problem = _load(os.path.join(log_dir, name))
        if problem:
            problems.append(problem)
        else:
            raw[name] = data
    return raw, problems


# Seconds a step took, logged into a file the grader reads. They measure the
# machine, not the design, and change from run to run, so the verifier keeps
# them out of result.json and writes them to timing.json (PLAN.md L.6,
# R2-G2). The detailed-route stage logs them when its antenna repair re-runs
# global routing. Named one by one, never by pattern; no graded key is here.
# The same stage also logs run_s and snapshot_batch_* keys; they were 0 in every
# run so far, so they stay, and the ship check's byte-identity line would show
# it if a design made them vary.
RUNTIME_KEYS = {
    "5_2_route.json": tuple(
        f"detailedroute__global_route__fastroute__{name}"
        for name in ("congestion_rsmt_s", "finalization_s", "initial_rsmt_s", "monotonic_s",
                     "new_route_l_s", "overflow_iterations_s", "route_l_s", "route_z_s",
                     "spiral_s")),
}


def split_runtime(raw):
    """(raw without the RUNTIME_KEYS pairs, {file: those pairs in file order})."""
    kept, runtime = {}, {}
    for name, pairs in raw.items():
        names = RUNTIME_KEYS.get(name, ())
        kept[name] = [[k, v] for k, v in pairs if k not in names]
        taken = [[k, v] for k, v in pairs if k in names]
        if taken:
            runtime[name] = taken
    return kept, runtime


def read_values(log_dir):
    """Return ({grader name: value}, problems). Values are passed through
    unchanged, NaN included, so the grader can reject them."""
    raw, problems = read_raw(log_dir)
    values = {}
    for name, (filename, key) in ORFS_KEYS.items():
        pairs = raw.get(filename)
        if pairs is None:
            continue
        found = [value for k, value in pairs if k == key]
        if len(found) == 1:
            values[name] = found[0]
        elif len(found) > 1 and key in LAST_OCCURRENCE_KEYS:
            values[name] = found[-1]
        elif len(found) > 1:
            problems.append(f"{filename}: {key} appears {len(found)} times")
    return values, problems


def parse_checks(text, clock_name):
    """Parse sg_checks.tcl output. Returns (values, problems).

    Expected lines:
      SG_CLOCK <name> <period>
      SG_UNCONSTRAINED_BEGIN
      [Warning: There are N unconstrained endpoints.
         <pin>  (one indented line per endpoint)]
      SG_UNCONSTRAINED_END
    """
    values, problems = {}, []
    periods = []
    inside, seen_block, header_count, listed = False, False, None, 0
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith("SG_CLOCK "):
            parts = line.split()
            if len(parts) == 3 and parts[1] == clock_name:
                periods.append(parts[2])
        elif stripped == "SG_UNCONSTRAINED_BEGIN":
            inside, seen_block = True, True
        elif stripped == "SG_UNCONSTRAINED_END":
            inside = False
        elif inside and stripped:
            match = _UNCON_HEADER.match(stripped)
            if match:
                if header_count is None:
                    header_count = int(match.group(1))
                else:
                    problems.append("unconstrained endpoints: header reported twice")
            elif header_count is not None and line[:1].isspace():
                listed += 1
            else:
                problems.append(f"unconstrained endpoints: unexpected text {stripped!r}")
    if not periods:
        problems.append(f"clock {clock_name}: not reported")
    elif len(periods) > 1:
        problems.append(f"clock {clock_name}: reported twice")
    else:
        try:
            values["clock_period_ns"] = float(periods[0])
        except ValueError:
            problems.append(f"clock {clock_name}: bad period {periods[0]!r}")
    if not seen_block or inside:
        problems.append("unconstrained endpoints: not reported")
    elif any(p.startswith("unconstrained endpoints") for p in problems):
        pass
    elif header_count is None:
        if not any(p.startswith("unconstrained endpoints") for p in problems):
            values["unconstrained_endpoints"] = 0
    elif header_count != listed:
        problems.append(
            f"unconstrained endpoints: header says {header_count}, list has {listed}"
        )
    else:
        values["unconstrained_endpoints"] = header_count
    return values, problems


# What a preview shows (PLAN.md HT.5 D): four numbers at the end of global
# routing, read by name from 5_1_grt.json. Never graded; the verifier keeps
# its own rebuild's values as a diagnostic so calibration can compare a
# preview with sign-off. Other keys repeat in this file; these four do not,
# and a repeat of one of them is a problem.
PREVIEW_FILE = "5_1_grt.json"
PREVIEW_KEYS = {
    "setup_ws_ns": "globalroute__timing__setup__ws",
    "hold_ws_ns": "globalroute__timing__hold__ws",
    "power_w": "globalroute__power__total",
    "area_um2": "globalroute__design__instance__area__stdcell",
}


def read_preview(log_dir):
    """Return ({name: value}, problems) from 5_1_grt.json."""
    pairs, problem = _load(os.path.join(log_dir, PREVIEW_FILE))
    if problem:
        return {}, [problem]
    values, problems = {}, []
    for name, key in PREVIEW_KEYS.items():
        found = [value for k, value in pairs if k == key]
        if len(found) == 1:
            values[name] = found[0]
        elif len(found) > 1:
            problems.append(f"{PREVIEW_FILE}: {key} appears {len(found)} times")
    return values, problems


# The rebuild's worst setup path, from its own final report (PLAN.md SB.5 A):
# a zero-weight diagnostic the meter's cards compare with what a model named.
# report_metrics writes one path per path group into the section headed
# "<stage> report_checks -path_delay max"; the path with the least slack is
# the worst. Never graded, never turned into a guess.
FINISH_REPORT = "6_finish.rpt"
_MAX_SECTION = re.compile(r"^\S+ report_checks -path_delay max$")
_SLACK_LINE = re.compile(r"^\s*(-?[0-9]+(?:\.[0-9]+)?)\s+slack \([A-Z]+\)\s*$")


def read_worst_setup_path(reports_dir):
    """Return ({startpoint, endpoint, path_group, slack_ns}, []) for the least-slack
    path of 6_finish.rpt's max-delay section, or (None, [problem])."""
    path = os.path.join(reports_dir, FINISH_REPORT)
    if not os.path.isfile(path):
        return None, [f"{FINISH_REPORT}: missing"]
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    starts = [i for i, line in enumerate(lines) if _MAX_SECTION.match(line.strip())]
    if not starts:
        return None, [f"{FINISH_REPORT}: no report_checks -path_delay max section"]
    paths, current = [], None
    for line in lines[starts[0] + 1:]:
        stripped = line.strip()
        if stripped.startswith("====="):
            break
        if stripped.startswith("Startpoint:"):
            current = {"startpoint": stripped.split()[1] if len(stripped.split()) > 1 else None,
                       "endpoint": None, "path_group": None}
        elif current is not None and stripped.startswith("Endpoint:") and len(stripped.split()) > 1:
            current["endpoint"] = stripped.split()[1]
        elif current is not None and stripped.startswith("Path Group:"):
            current["path_group"] = stripped.split(":", 1)[1].strip()
        elif current is not None and _SLACK_LINE.match(line):
            current["slack_ns"] = float(_SLACK_LINE.match(line).group(1))
            if current["startpoint"] and current["endpoint"]:
                paths.append(current)
            current = None
    if not paths:
        return None, [f"{FINISH_REPORT}: no path in the max section"]
    return min(paths, key=lambda item: item["slack_ns"]), []


DRC_REPORT = "6_drc.lyrdb"
LVS_LOG = "6_lvs.log"
LVS_MATCH_LINE = "INFO : Congratulations! Netlists match."
LVS_MISMATCH_LINE = "ERROR : Netlists don't match"


def _read_drc(path):
    """Return (count, {rule: count}, problem) from a KLayout report database."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        return None, None, f"{name}: missing"
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, UnicodeDecodeError):
        return None, None, f"{name}: not valid XML"
    if root.tag != "report-database":
        return None, None, f"{name}: not a KLayout report database"
    sections = root.findall("items")
    if not sections:
        return None, None, f"{name}: no items section"
    if len(sections) > 1:
        return None, None, f"{name}: more than one items section"
    by_rule = {}
    items = sections[0].findall("item")
    for item in items:
        rule = (item.findtext("category") or "").strip().strip("'")
        by_rule[rule] = by_rule.get(rule, 0) + 1
    return len(items), dict(sorted(by_rule.items())), None


def _read_lvs(path):
    """Return (match: bool, problem) from the LVS log's verdict line."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        return None, f"{name}: missing"
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = [line.strip() for line in handle]
    matches = lines.count(LVS_MATCH_LINE)
    mismatches = lines.count(LVS_MISMATCH_LINE)
    if matches + mismatches == 0:
        return None, f"{name}: no LVS verdict"
    if matches + mismatches > 1:
        return None, f"{name}: LVS verdict reported more than once"
    return matches == 1, None


def read_signoff(reports_dir, logs_dir):
    """Return (values, details, problems) for KLayout DRC and LVS.

    values: klayout_drc_count (int) and lvs_match (bool), each present only
    when it could be read. details: klayout_drc_by_rule. A missing or unclear
    file is a problem, never a zero or a match."""
    values, details, problems = {}, {}, []
    count, by_rule, problem = _read_drc(os.path.join(reports_dir, DRC_REPORT))
    if problem:
        problems.append(problem)
    else:
        values["klayout_drc_count"] = count
        details["klayout_drc_by_rule"] = by_rule
    match, problem = _read_lvs(os.path.join(logs_dir, LVS_LOG))
    if problem:
        problems.append(problem)
    else:
        values["lvs_match"] = match
    return values, details, problems
