"""How the RTL gates combine with grader 7's own verdict (PLAN.md F2.5 F).
Written before the code."""

import copy

import pytest

from sg import score
from sg.rtl import gates

TARGETS = {
    "clock_period_ns": 5.0, "power_max_w": 0.02, "area_max_um2": 80000.0, "flow_run_cap": 3,
    "setup_margin_ns": 0.025, "power_margin": 0.01, "area_margin": 0.005, "preview_cap": 12,
    "weights": {"timing": 1.0, "routing": 1.0, "power": 1.0, "area": 1.0},
}


def values(**changes):
    base = {"clock_period_ns": 5.0, "setup_ws_ns": 0.1, "hold_ws_ns": 0.3, "route_drc_count": 0,
            "antenna_nets": 0, "antenna_pins": 0, "unconstrained_endpoints": 0, "power_w": 0.019,
            "area_um2": 75000.0, "klayout_drc_count": 0, "lvs_match": True, "setup_tns_ns": 0.0,
            "max_slew_violations": 0, "max_cap_violations": 0, "max_fanout_violations": 0}
    base.update(changes)
    return base


def facts(**changes):
    base = {"agent_status": "completed", "patch_present": True, "infra_error": None, "verifier_error": None,
            "allowed_edits_valid": True, "locked_files_intact": True, "run_log_intact": True,
            "flow_exit_code": 0, "flow_runs": 1, "preview_runs": 0, "resource_limit": None}
    base.update(changes)
    return base


def flow(run=None, vals=None):
    return score.score({"run": run or facts(), "values": vals or values(), "problems": []}, TARGETS)


PASSING_RTL = {"rtl_files_legal": True, "answer_valid": True, "rtl_lint_clean": True, "ports_locked": True,
               "rtl_equivalent": True}


def test_every_rtl_gate_passing_keeps_grader_sevens_pass():
    result = gates.combine(dict(PASSING_RTL), flow())
    assert result["state"] == "PASS" and result["reward"] == 1.0 and result["gates_passed"] is True
    assert list(result["gates"])[:5] == list(gates.RTL_GATES)
    assert len(result["gates"]) == len(gates.RTL_GATES) + len(score.GATES)
    # Version 4: the registered-ports rule checked again on the finished netlist (PLAN.md P.6 4 Oct 19:51);
    # v3 tasks keep "3" (sg/f2v3/rtl/gates.py: the lint without the table fold and with the reset rule,
    # PLAN.md V3.5 A and B), v2 tasks "2" (sg/f2v2/, the registered-ports rule), v1 tasks "1" (sg/f2v1/).
    assert result["grader_version"] == score.GRADER_VERSION and result["rtl_grader_version"] == "4"
    import os
    root = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    for version in ("1", "2", "3"):
        with open(os.path.join(root, "sg", f"f2v{version}", "rtl", "gates.py"), encoding="utf-8") as handle:
            assert f'RTL_GRADER_VERSION = "{version}"' in handle.read()


@pytest.mark.parametrize("failed", list(gates.RTL_GATES))
def test_a_failed_rtl_gate_fails_the_run_with_no_flow_and_later_rtl_gates_not_evaluated(failed):
    rtl = dict(PASSING_RTL)
    order = list(gates.RTL_GATES)
    rtl[failed] = False
    for later in order[order.index(failed) + 1:]:
        rtl[later] = None
    no_flow = flow(facts(flow_exit_code=None, flow_runs=0))
    result = gates.combine(rtl, no_flow)
    assert result["state"] == "FAIL" and result["reward"] == 0.0 and result["gates_passed"] is False
    assert result["diagnostics"]["gates_failed"][0] == failed
    assert result["gates"]["flow_finished"] is None


def test_a_passing_flow_with_an_rtl_gate_failed_is_never_a_pass():
    for name in gates.RTL_GATES:
        rtl = dict(PASSING_RTL, **{name: False})
        result = gates.combine(rtl, flow())
        assert result["state"] == "FAIL" and result["reward"] == 0.0


def test_an_rtl_gate_not_evaluated_is_never_counted_as_yes():
    rtl = dict(PASSING_RTL, rtl_equivalent=None)
    result = gates.combine(rtl, flow())
    assert result["state"] == "FAIL" and result["reward"] == 0.0


def test_a_missing_or_malformed_rtl_verdict_is_a_verifier_error():
    for broken in ({}, {k: v for k, v in PASSING_RTL.items() if k != "ports_locked"},
                   dict(PASSING_RTL, rtl_lint_clean="yes")):
        result = gates.combine(broken, flow())
        assert result["state"] == "VERIFIER_ERROR" and result["reward"] == 0.0


def test_infra_and_verifier_errors_from_the_flow_stay_what_they_are():
    infra = flow(facts(infra_error="docker died"))
    assert gates.combine(dict(PASSING_RTL), infra)["state"] == "INFRA_ERROR"
    broken = flow(facts(verifier_error="crash"))
    assert gates.combine(dict(PASSING_RTL), broken)["state"] == "VERIFIER_ERROR"


def test_a_flow_failure_stays_a_failure_and_partial_credit_survives_only_with_every_gate():
    slow = flow(vals=values(setup_ws_ns=-0.2))
    result = gates.combine(dict(PASSING_RTL), slow)
    assert result["state"] == "FAIL" and "setup_met" in result["diagnostics"]["gates_failed"]
    heavy = flow(vals=values(power_w=0.03))
    result = gates.combine(dict(PASSING_RTL), heavy)
    assert result["state"] == "FAIL" and 0 < result["reward"] < 1


def test_combine_does_not_change_its_inputs():
    rtl, base = dict(PASSING_RTL), flow()
    before = copy.deepcopy(base)
    gates.combine(rtl, base)
    assert base == before and rtl == PASSING_RTL


def test_a_timed_out_agent_keeps_its_state_with_a_failed_verdict():
    timed_out = flow(facts(agent_status="timeout", flow_exit_code=None, flow_runs=0))
    result = gates.combine(dict(PASSING_RTL, rtl_lint_clean=False, ports_locked=None, rtl_equivalent=None),
                           timed_out)
    assert result["state"] == "AGENT_TIMEOUT_WITH_PATCH" and result["verdict"] == "FAIL"
    assert result["reward"] == 0.0


# Version 4 (PLAN.md P.6 4 Oct 19:51): one gate after the flow, on the finished netlist.

def test_the_netlist_gate_comes_after_the_flows_gates_and_a_false_one_fails_the_run():
    result = gates.combine(dict(PASSING_RTL), flow(), netlist={"netlist_ports_registered": True})
    assert result["state"] == "PASS" and result["reward"] == 1.0
    assert list(result["gates"])[-1] == "netlist_ports_registered" and list(gates.NETLIST_GATES) == [
        "netlist_ports_registered"]
    failed = gates.combine(dict(PASSING_RTL), flow(), netlist={"netlist_ports_registered": False})
    assert failed["state"] == "FAIL" and failed["reward"] == 0.0 and failed["gates_passed"] is False
    assert "netlist_ports_registered" in failed["diagnostics"]["gates_failed"]


def test_a_netlist_gate_not_evaluated_is_never_a_pass():
    result = gates.combine(dict(PASSING_RTL), flow(), netlist={"netlist_ports_registered": None})
    assert result["state"] != "PASS" and result["reward"] == 0.0
    # With the flow already failing, the run stays what it was.
    bad_flow = flow(run=facts(flow_exit_code=2))
    result = gates.combine(dict(PASSING_RTL), bad_flow, netlist={"netlist_ports_registered": None})
    assert result["state"] == "FAIL" and result["reward"] == 0.0


def test_a_missing_or_malformed_netlist_verdict_is_a_verifier_error():
    for netlist in ({}, {"netlist_ports_registered": "yes"}):
        result = gates.combine(dict(PASSING_RTL), flow(), netlist=netlist)
        assert result["state"] == "VERIFIER_ERROR" and result["reward"] == 0.0, netlist


def test_partial_credit_needs_the_netlist_gate_too():
    over = flow(vals=values(power_w=0.0199))
    assert over["state"] == "FAIL" and 0.0 < over["reward"] < 1.0
    kept = gates.combine(dict(PASSING_RTL), over, netlist={"netlist_ports_registered": True})
    assert kept["reward"] == over["reward"]
    for verdict in (None, False):
        result = gates.combine(dict(PASSING_RTL), over, netlist={"netlist_ports_registered": verdict})
        assert result["reward"] == 0.0 and result["state"] != "PASS", verdict
