"""Tests for sg.score (PLAN.md P1.2 to P1.4). Written before the code."""

import copy
import json
import math
import os

import pytest

from sg import metrics as metrics_mod
from sg.score import BOOL_METRICS, GATES, GRADER_VERSION, REQUIRED_METRICS, STATES, score

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "gcd_stock_1p1")

TARGETS = {
    "clock_period_ns": 1.1,
    "power_max_w": 0.012,
    "area_max_um2": 6000.0,
    # PLAN.md HT.5 C: 0.5% of 1.1 ns rounded up to a picosecond, 1%, 0.5%.
    "setup_margin_ns": 0.006,
    "power_margin": 0.01,
    "area_margin": 0.005,
    "flow_run_cap": 8,
    "preview_cap": 12,
    "weights": {"timing": 1.0, "routing": 1.0, "power": 1.0, "area": 1.0},
}
POWER_LINE = TARGETS["power_max_w"] * (1 - TARGETS["power_margin"])
AREA_LINE = TARGETS["area_max_um2"] * (1 - TARGETS["area_margin"])
NO_MARGIN = dict(TARGETS, setup_margin_ns=0.0, power_margin=0.0, area_margin=0.0)


def run_ok(**changes):
    run = {
        "agent_status": "completed",
        "patch_present": True,
        "infra_error": None,
        "verifier_error": None,
        "allowed_edits_valid": True,
        "locked_files_intact": True,
        "run_log_intact": True,
        "flow_exit_code": 0,
        "flow_runs": 2,
        "preview_runs": 3,
        "resource_limit": None,
    }
    run.update(changes)
    return run


def stock_values():
    values, problems = metrics_mod.read_values(FIXTURE)
    assert problems == []
    values["clock_period_ns"] = 1.1
    values["unconstrained_endpoints"] = 0
    # KLayout sign-off on the same stock run (run 1, P0.5): 0 DRC, LVS match.
    values["klayout_drc_count"] = 0
    values["lvs_match"] = True
    return values


def closing_values(**changes):
    values = stock_values()
    values.update(setup_ws_ns=0.05, setup_tns_ns=0.0, power_w=0.0100,
                  area_um2=5500.0)
    values.update(changes)
    return values


def build(values=None, run=None, problems=None):
    return {
        "run": run if run is not None else run_ok(),
        "values": values if values is not None else closing_values(),
        "problems": problems or [],
    }


def gates_except(result, name):
    return {k: v for k, v in result["gates"].items() if k != name}


# The recorded Phase 0 run


def test_stock_gcd_misses_setup_and_nothing_else():
    result = score(build(stock_values()), TARGETS)
    assert result["state"] == "FAIL"
    assert result["verdict"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["gates"]["setup_met"] is False
    assert all(gates_except(result, "setup_met").values())
    timing = result["components"]["timing"]
    # Short of the 0.006 ns margin, not of zero (PLAN.md HT.5 C).
    assert timing["loss"] == pytest.approx(math.log(1 + (1.45954 + 0.006) / 1.1))
    assert result["diagnostics"]["setup_short_ns"] == pytest.approx(1.45954 + 0.006)
    assert result["diagnostics"]["setup_tns_ns"] == pytest.approx(-64.9417)


def test_closing_run_passes_with_reward_one():
    result = score(build(), TARGETS)
    assert result["state"] == "PASS"
    assert result["verdict"] == "PASS"
    assert result["reward"] == 1.0
    assert all(result["gates"].values())
    assert list(result["gates"]) == list(GATES)
    for name in ("timing", "routing", "power", "area"):
        assert result["components"][name]["met"] is True
        assert result["components"][name]["score"] == 1.0


# Each gate failing alone


@pytest.mark.parametrize("name, values_change, run_change", [
    ("allowed_edits_legal", {}, {"allowed_edits_valid": False}),
    ("locked_files_intact", {}, {"locked_files_intact": False}),
    ("clock_period_locked", {"clock_period_ns": 2.0}, {}),
    ("route_drc_zero", {"route_drc_count": 1}, {}),
    ("antenna_zero", {"antenna_nets": 1}, {}),
    ("antenna_zero", {"antenna_pins": 1}, {}),
    ("klayout_drc_clean", {"klayout_drc_count": 1}, {}),
    ("lvs_clean", {"lvs_match": False}, {}),
    ("setup_met", {"setup_ws_ns": -0.001}, {}),
    ("hold_met", {"hold_ws_ns": -0.001}, {}),
    ("no_unconstrained_endpoints", {"unconstrained_endpoints": 1}, {}),
    ("electrical_rules_clean", {"max_slew_violations": 1}, {}),
    ("electrical_rules_clean", {"max_cap_violations": 1}, {}),
    ("electrical_rules_clean", {"max_fanout_violations": 1}, {}),
])
def test_each_gate_failing_alone(name, values_change, run_change):
    result = score(build(closing_values(**values_change), run_ok(**run_change)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["gates"][name] is False
    assert all(gates_except(result, name).values())


def test_flow_not_finished_fails_and_leaves_metric_gates_unevaluated():
    result = score(build({}, run_ok(flow_exit_code=2)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["gates"]["flow_finished"] is False
    for name in GATES[GATES.index("flow_finished") + 1:]:
        assert result["gates"][name] is None
    assert result["components"] is None


def test_flow_not_run_after_illegal_edits():
    result = score(build({}, run_ok(allowed_edits_valid=False, flow_exit_code=None)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["gates"]["allowed_edits_legal"] is False
    assert result["gates"]["flow_finished"] is None


# Missing, unreadable and odd metrics


@pytest.mark.parametrize("key", REQUIRED_METRICS)
def test_missing_required_metric_is_verifier_error(key):
    values = closing_values()
    del values[key]
    result = score(build(values), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert result["reward"] == 0.0
    assert result["verdict"] is None
    assert any(key in e for e in result["errors"])


NUMERIC_METRICS = [key for key in REQUIRED_METRICS if key not in BOOL_METRICS]


@pytest.mark.parametrize("key", NUMERIC_METRICS)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "N/A", None, True])
def test_unreadable_required_metric_is_verifier_error(key, bad):
    result = score(build(closing_values(**{key: bad})), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert result["reward"] == 0.0


@pytest.mark.parametrize("key, bad", [
    ("route_drc_count", -1), ("route_drc_count", 1.5), ("antenna_nets", -2),
    ("unconstrained_endpoints", 0.5), ("power_w", 0.0), ("power_w", -1e-3),
    ("area_um2", 0), ("clock_period_ns", -1.1),
    ("klayout_drc_count", -1), ("klayout_drc_count", 0.5),
    ("max_slew_violations", -1), ("max_slew_violations", 0.5), ("max_cap_violations", -3),
    ("max_cap_violations", 1.25), ("max_fanout_violations", -1), ("max_fanout_violations", 2.5),
])
def test_impossible_metric_values_are_verifier_error(key, bad):
    result = score(build(closing_values(**{key: bad})), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"


def test_reading_problem_with_missing_value_is_reported():
    values = closing_values()
    del values["power_w"]
    result = score(build(values, problems=["6_report.json: not valid JSON"]), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert "6_report.json: not valid JSON" in result["errors"]


def test_missing_metrics_after_a_failed_flow_are_not_an_error():
    result = score(build({"setup_ws_ns": -1.0}, run_ok(flow_exit_code=1)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["errors"] == []


# On target and just past it


def test_exactly_on_every_pass_line_passes():
    values = closing_values(setup_ws_ns=TARGETS["setup_margin_ns"], hold_ws_ns=0.0,
                            power_w=POWER_LINE, area_um2=AREA_LINE)
    result = score(build(values), TARGETS)
    assert result["state"] == "PASS"
    assert result["reward"] == 1.0


def test_negative_zero_slack_is_on_target_without_a_margin():
    result = score(build(closing_values(setup_ws_ns=-0.0, hold_ws_ns=-0.0)), NO_MARGIN)
    assert result["state"] == "PASS"


def test_just_past_power_target_fails_with_partial_reward():
    power = math.nextafter(POWER_LINE, math.inf)
    result = score(build(closing_values(power_w=power)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["components"]["power"]["met"] is False
    assert all(result["gates"].values())
    assert 0.0 < result["reward"] < 1.0


def test_just_past_area_target_fails():
    area = math.nextafter(AREA_LINE, math.inf)
    result = score(build(closing_values(area_um2=area)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["components"]["area"]["met"] is False


def test_just_past_setup_target_fails():
    result = score(build(closing_values(setup_ws_ns=-1e-12)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["gates"]["setup_met"] is False
    assert result["reward"] == 0.0


def test_partial_reward_follows_closer_bench_formula():
    result = score(build(closing_values(power_w=0.024, area_um2=12000.0)), NO_MARGIN)
    assert result["state"] == "FAIL"
    assert result["reward"] == pytest.approx(math.exp(-2 * math.log(2)))
    assert result["diagnostics"]["ppa_score_without_gates"] == pytest.approx(0.25)


def test_partial_reward_counts_from_the_margined_lines():
    result = score(build(closing_values(power_w=0.024, area_um2=12000.0)), TARGETS)
    expected = math.exp(-math.log(0.024 / POWER_LINE) - math.log(12000.0 / AREA_LINE))
    assert result["reward"] == pytest.approx(expected)
    assert result["components"]["power"]["target"] == pytest.approx(POWER_LINE)
    assert result["components"]["area"]["target"] == pytest.approx(AREA_LINE)


def test_weight_zero_component_does_not_block_pass():
    targets = copy.deepcopy(TARGETS)
    targets["weights"]["area"] = 0.0
    result = score(build(closing_values(area_um2=1e6)), targets)
    assert result["state"] == "PASS"
    assert result["reward"] == 1.0


# Run states


def test_infra_failure_is_its_own_state_even_with_perfect_metrics():
    result = score(build(run=run_ok(infra_error="docker create failed")), TARGETS)
    assert result["state"] == "INFRA_ERROR"
    assert result["reward"] == 0.0
    assert result["verdict"] is None
    assert all(v is None for v in result["gates"].values())


def test_agent_error_is_its_own_state():
    result = score(build(run=run_ok(agent_status="error")), TARGETS)
    assert result["state"] == "AGENT_ERROR"
    assert result["reward"] == 0.0


def test_timeout_with_passing_patch_keeps_its_score():
    result = score(build(run=run_ok(agent_status="timeout")), TARGETS)
    assert result["state"] == "AGENT_TIMEOUT_WITH_PATCH"
    assert result["verdict"] == "PASS"
    assert result["reward"] == 1.0


def test_timeout_with_failing_patch_keeps_its_verdict():
    result = score(build(stock_values(), run_ok(agent_status="timeout")), TARGETS)
    assert result["state"] == "AGENT_TIMEOUT_WITH_PATCH"
    assert result["verdict"] == "FAIL"
    assert result["reward"] == 0.0


def test_timeout_without_patch_is_agent_error():
    result = score(build(run=run_ok(agent_status="timeout", patch_present=False)), TARGETS)
    assert result["state"] == "AGENT_ERROR"


def test_verifier_crash_is_verifier_error():
    result = score(build(run=run_ok(verifier_error="KeyError in verify.py")), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert result["reward"] == 0.0


@pytest.mark.parametrize("key", ["agent_status", "flow_exit_code", "locked_files_intact"])
def test_missing_run_fact_is_verifier_error(key):
    run = run_ok()
    del run[key]
    assert score(build(run=run), TARGETS)["state"] == "VERIFIER_ERROR"


def test_unknown_agent_status_is_verifier_error():
    assert score(build(run=run_ok(agent_status="maybe")), TARGETS)["state"] == "VERIFIER_ERROR"


@pytest.mark.parametrize("change", [
    {"power_max_w": None}, {"area_max_um2": 0}, {"clock_period_ns": float("nan")},
    {"weights": {"timing": 1, "routing": 1, "power": 1}},
    {"weights": {"timing": 1, "routing": 1, "power": -1, "area": 1}},
])
def test_bad_targets_are_verifier_error(change):
    targets = dict(copy.deepcopy(TARGETS), **change)
    if change.get("power_max_w", 1) is None:
        del targets["power_max_w"]
    assert score(build(), targets)["state"] == "VERIFIER_ERROR"


def test_the_seven_states_are_distinct_names():
    assert STATES == ("PASS", "FAIL", "AGENT_ERROR", "AGENT_TIMEOUT_WITH_PATCH",
                      "VERIFIER_ERROR", "INFRA_ERROR", "RESOURCE_LIMIT")


# Purity and output shape


def test_score_is_pure_and_repeatable():
    metrics = build(stock_values())
    before = copy.deepcopy(metrics)
    targets = copy.deepcopy(TARGETS)
    first = score(metrics, targets)
    second = score(metrics, targets)
    assert first == second
    assert metrics == before
    assert targets == TARGETS


def test_result_is_strict_json_even_with_nan_input():
    result = score(build(closing_values(power_w=float("nan"))), TARGETS)
    text = json.dumps(result, allow_nan=False, sort_keys=True)
    assert '"NaN"' in text


def test_flow_run_count_is_reported():
    assert score(build(run=run_ok(flow_runs=7)), TARGETS)["flow_runs"] == 7


# Added after the mutation check: two mutants survived the first suite.


def test_reward_one_means_pass_and_only_pass():
    targets = copy.deepcopy(TARGETS)
    targets["weights"]["power"] = 1e-20
    power = TARGETS["power_max_w"] * 1.001
    result = score(build(closing_values(power_w=power)), targets)
    assert result["diagnostics"]["ppa_score_without_gates"] == 1.0
    assert result["state"] == "FAIL"
    assert result["reward"] < 1.0


def test_unevaluated_gate_never_counts_as_passed():
    result = score(build(closing_values(), run_ok(flow_exit_code=None)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["gates"]["flow_finished"] is None
    assert result["gates_passed"] is False
    assert result["reward"] == 0.0


# KLayout sign-off gates (PLAN.md R2-A1)


def test_bool_metrics_are_exactly_the_lvs_flag():
    assert BOOL_METRICS == ("lvs_match",)
    assert set(BOOL_METRICS) <= set(REQUIRED_METRICS)


@pytest.mark.parametrize("bad", [1, 0, "True", "yes", None, float("nan")])
def test_lvs_flag_must_be_a_real_boolean(bad):
    result = score(build(closing_values(lvs_match=bad)), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert result["reward"] == 0.0
    assert any("lvs_match" in e for e in result["errors"])


def test_signoff_gates_sit_after_routing_and_before_timing():
    order = list(GATES)
    assert order.index("antenna_zero") < order.index("klayout_drc_clean")
    assert order.index("klayout_drc_clean") < order.index("lvs_clean") < order.index("setup_met")


def test_klayout_violations_count_in_the_routing_component():
    result = score(build(closing_values(klayout_drc_count=3)), TARGETS)
    routing = result["components"]["routing"]
    assert routing["value"] == 4
    assert routing["loss"] == pytest.approx(math.log(4))
    assert result["gates"]["klayout_drc_clean"] is False
    assert result["reward"] == 0.0
    assert result["diagnostics"]["klayout_drc_count"] == 3


def test_lvs_mismatch_is_reported_in_diagnostics():
    result = score(build(closing_values(lvs_match=False)), TARGETS)
    assert result["diagnostics"]["lvs_match"] is False
    assert result["diagnostics"]["gates_failed"] == ["lvs_clean"]


def test_grader_version_is_seven_since_the_electrical_rules():
    assert GRADER_VERSION == "7"
    assert score(build(), TARGETS)["grader_version"] == "7"


# The run log and the budget (PLAN.md R2-A2, R2-A3)


def test_run_log_gates_sit_right_after_the_locked_files():
    order = list(GATES)
    assert order[order.index("locked_files_intact") + 1:order.index("locked_files_intact") + 3] == [
        "run_log_intact", "within_budget"]
    assert order.index("within_budget") < order.index("flow_finished")


def test_broken_run_log_fails_and_budget_is_not_evaluated():
    result = score(build(run=run_ok(run_log_intact=False, flow_runs=None)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["gates"]["run_log_intact"] is False
    assert result["gates"]["within_budget"] is None
    assert "run_log_intact" in result["diagnostics"]["gates_failed"]


def test_runs_past_the_cap_fail_even_with_perfect_metrics():
    result = score(build(run=run_ok(flow_runs=9)), TARGETS)
    assert result["state"] == "FAIL"
    assert result["gates"]["within_budget"] is False
    assert result["reward"] == 0.0


@pytest.mark.parametrize("runs", [0, 1, 8])
def test_runs_up_to_the_cap_are_within_budget(runs):
    result = score(build(run=run_ok(flow_runs=runs)), TARGETS)
    assert result["gates"]["within_budget"] is True
    assert result["state"] == "PASS"
    assert result["flow_runs"] == runs


@pytest.mark.parametrize("runs", [None, -1, 1.0, True, "2"])
def test_intact_log_needs_a_whole_run_count(runs):
    result = score(build(run=run_ok(flow_runs=runs)), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"


def test_run_log_fact_must_be_true_or_false():
    assert score(build(run=run_ok(run_log_intact="yes")), TARGETS)["state"] == "VERIFIER_ERROR"
    run = run_ok()
    del run["run_log_intact"]
    assert score(build(run=run), TARGETS)["state"] == "VERIFIER_ERROR"


@pytest.mark.parametrize("cap", [None, 0, -3, 2.5, True, "8"])
def test_flow_run_cap_must_be_a_positive_whole_number(cap):
    targets = dict(copy.deepcopy(TARGETS), flow_run_cap=cap)
    if cap is None:
        del targets["flow_run_cap"]
    result = score(build(), targets)
    assert result["state"] == "VERIFIER_ERROR"
    assert any("flow_run_cap" in e for e in result["errors"])


# Per-stage caps (PLAN.md T0.1): a rebuild stopped by a cap is RESOURCE_LIMIT.

CAPPED = "stage route passed its wall-clock cap of 1800 s"


def capped_run(**changes):
    return run_ok(**dict({"resource_limit": CAPPED, "flow_exit_code": None}, **changes))


def test_a_capped_rebuild_is_its_own_state_and_never_scores():
    # Even with perfect metrics lying around, a capped rebuild is not a pass.
    result = score(build(closing_values(), capped_run()), TARGETS)
    assert result["state"] == "RESOURCE_LIMIT"
    assert result["verdict"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["errors"] == [f"resource: {CAPPED}"]
    assert result["gates"]["flow_finished"] is False
    for name in ("allowed_edits_legal", "locked_files_intact", "run_log_intact", "within_budget"):
        assert result["gates"][name] is True
    for name in GATES[GATES.index("flow_finished") + 1:]:
        assert result["gates"][name] is None
    assert result["gates_passed"] is False
    assert result["components"] is None
    assert result["diagnostics"]["gates_failed"] == ["flow_finished"]


def test_a_capped_rebuild_is_not_a_timeout_with_patch():
    result = score(build(closing_values(), capped_run(agent_status="timeout")), TARGETS)
    assert result["state"] == "RESOURCE_LIMIT"
    assert result["reward"] == 0.0


def test_machine_failure_still_comes_first():
    result = score(build(run=capped_run(infra_error="docker create failed")), TARGETS)
    assert result["state"] == "INFRA_ERROR"


@pytest.mark.parametrize("bad", ["", 3, True, ["x"]])
def test_resource_limit_is_none_or_a_text(bad):
    result = score(build(run=capped_run(resource_limit=bad)), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"


def test_a_capped_rebuild_cannot_also_have_an_exit_code():
    result = score(build(run=capped_run(flow_exit_code=0)), TARGETS)
    assert result["state"] == "VERIFIER_ERROR"
    assert any("resource_limit" in e for e in result["errors"])


def test_resource_limit_is_a_required_run_fact():
    run = run_ok()
    del run["resource_limit"]
    assert score(build(run=run), TARGETS)["state"] == "VERIFIER_ERROR"


# The noise margin (PLAN.md HT.5 C): setup at least setup_margin_ns, power at
# most (1 - power_margin) x the cap, area at most (1 - area_margin) x the cap,
# hold at least 0.


def test_a_razor_edge_pass_no_longer_counts():
    # DeepSeek's jpeg pass in run 3: +0.000359 ns against a 0.027 ns margin.
    targets = dict(TARGETS, clock_period_ns=5.3, setup_margin_ns=0.027)
    result = score(build(closing_values(setup_ws_ns=0.000359, clock_period_ns=5.3)), targets)
    assert result["state"] == "FAIL"
    assert result["gates"]["setup_met"] is False
    assert result["reward"] == 0.0


def test_setup_just_under_the_margin_fails_and_at_it_passes():
    margin = TARGETS["setup_margin_ns"]
    assert score(build(closing_values(setup_ws_ns=margin)), TARGETS)["state"] == "PASS"
    under = score(build(closing_values(setup_ws_ns=math.nextafter(margin, -math.inf))), TARGETS)
    assert under["state"] == "FAIL" and under["gates"]["setup_met"] is False


def test_slack_between_zero_and_the_margin_is_short_by_the_difference():
    result = score(build(closing_values(setup_ws_ns=0.002)), TARGETS)
    assert result["gates"]["setup_met"] is False
    assert result["diagnostics"]["setup_short_ns"] == pytest.approx(0.004)
    timing = result["components"]["timing"]
    assert timing["value"] == pytest.approx(1.1 + 0.004)
    assert timing["met"] is False


def test_hold_has_no_margin():
    result = score(build(closing_values(hold_ws_ns=0.0)), TARGETS)
    assert result["state"] == "PASS"
    assert score(build(closing_values(hold_ws_ns=-1e-9)), TARGETS)["gates"]["hold_met"] is False


@pytest.mark.parametrize("key, value, cap", [
    ("power_w", 0.0119, "power_max_w"), ("power_w", 0.012, "power_max_w"),
    ("area_um2", 5980.0, "area_max_um2"), ("area_um2", 6000.0, "area_max_um2"),
])
def test_under_the_cap_but_inside_the_margin_fails(key, value, cap):
    result = score(build(closing_values(**{key: value})), TARGETS)
    assert value <= TARGETS[cap]
    assert result["state"] == "FAIL"
    assert result["components"][key.split("_")[0]]["met"] is False
    assert 0.0 < result["reward"] < 1.0


@pytest.mark.parametrize("change", [
    {"setup_margin_ns": None}, {"setup_margin_ns": -0.001}, {"setup_margin_ns": float("nan")},
    {"setup_margin_ns": True}, {"power_margin": None}, {"power_margin": -0.01},
    {"power_margin": 1.0}, {"area_margin": None}, {"area_margin": 1.5}, {"area_margin": "0.005"},
])
def test_margins_are_required_targets(change):
    targets = dict(copy.deepcopy(TARGETS), **change)
    for key, value in change.items():
        if value is None:
            del targets[key]
    result = score(build(), targets)
    assert result["state"] == "VERIFIER_ERROR"
    assert any(key in e for key in change for e in result["errors"])


# Two budgets (PLAN.md HT.5 D): full runs and previews, each against its own cap.


@pytest.mark.parametrize("full, previews, ok", [
    (0, 0, True), (8, 12, True), (8, 0, True), (0, 12, True),
    (9, 0, False), (0, 13, False), (9, 13, False),
])
def test_within_budget_checks_both_budgets(full, previews, ok):
    result = score(build(run=run_ok(flow_runs=full, preview_runs=previews)), TARGETS)
    assert result["gates"]["within_budget"] is ok
    assert result["state"] == ("PASS" if ok else "FAIL")
    assert result["preview_runs"] == previews


def test_a_task_without_previews_fails_a_submission_that_used_one():
    targets = dict(TARGETS, preview_cap=0)
    assert score(build(run=run_ok(preview_runs=0)), targets)["state"] == "PASS"
    result = score(build(run=run_ok(preview_runs=1)), targets)
    assert result["state"] == "FAIL" and result["gates"]["within_budget"] is False


@pytest.mark.parametrize("previews", [None, -1, 1.0, True, "2"])
def test_intact_log_needs_a_whole_preview_count(previews):
    assert score(build(run=run_ok(preview_runs=previews)), TARGETS)["state"] == "VERIFIER_ERROR"


def test_preview_count_is_a_required_run_fact():
    run = run_ok()
    del run["preview_runs"]
    assert score(build(run=run), TARGETS)["state"] == "VERIFIER_ERROR"


@pytest.mark.parametrize("cap", [None, -1, 2.5, True, "12"])
def test_preview_cap_must_be_a_whole_number_of_zero_or_more(cap):
    targets = dict(copy.deepcopy(TARGETS), preview_cap=cap)
    if cap is None:
        del targets["preview_cap"]
    result = score(build(), targets)
    assert result["state"] == "VERIFIER_ERROR"
    assert any("preview_cap" in e for e in result["errors"])


def test_a_broken_log_leaves_both_budgets_unevaluated():
    result = score(build(run=run_ok(run_log_intact=False, flow_runs=None, preview_runs=None)), TARGETS)
    assert result["gates"]["within_budget"] is None and result["state"] == "FAIL"


# The kit's electrical limits (grader version 7, PLAN.md SB.5 A)

ELECTRICAL = ("max_slew_violations", "max_cap_violations", "max_fanout_violations")


def test_the_three_electrical_counts_are_required_counts():
    for key in ELECTRICAL:
        assert key in REQUIRED_METRICS
        assert key not in BOOL_METRICS


def test_electrical_gate_sits_after_hold_and_before_the_endpoint_check():
    assert GATES.index("electrical_rules_clean") == GATES.index("hold_met") + 1
    assert GATES.index("no_unconstrained_endpoints") == GATES.index("electrical_rules_clean") + 1


@pytest.mark.parametrize("key", ELECTRICAL)
@pytest.mark.parametrize("count", [1, 6, 1399])
def test_any_electrical_violation_fails_with_reward_zero(key, count):
    result = score(build(closing_values(**{key: count})), TARGETS)
    assert result["state"] == "FAIL"
    assert result["verdict"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["gates"]["electrical_rules_clean"] is False
    assert all(gates_except(result, "electrical_rules_clean").values())
    assert result["diagnostics"]["gates_failed"] == ["electrical_rules_clean"]
    assert result["diagnostics"][key] == count


def test_zero_electrical_violations_pass():
    result = score(build(closing_values(max_slew_violations=0, max_cap_violations=0,
                                        max_fanout_violations=0)), TARGETS)
    assert result["state"] == "PASS"
    assert result["gates"]["electrical_rules_clean"] is True


def test_task_1_v2_grader_6_reference_now_fails_on_the_electrical_rules_alone():
    # Its logged numbers at 8.8 ns under task 1 v2's targets (CALIBRATION.md): every
    # other gate and both caps passed, 181 max-slew and 6 max-capacitance violations.
    targets = dict(TARGETS, clock_period_ns=8.8, power_max_w=0.00854, area_max_um2=81000.0,
                   setup_margin_ns=0.044)
    values = closing_values(clock_period_ns=8.8, setup_ws_ns=0.125021, hold_ws_ns=0.631437,
                            power_w=0.0083686, area_um2=80183.2, max_slew_violations=181,
                            max_cap_violations=6, max_fanout_violations=0)
    result = score(build(values), targets)
    assert result["state"] == "FAIL"
    assert result["reward"] == 0.0
    assert result["diagnostics"]["gates_failed"] == ["electrical_rules_clean"]
    assert result["diagnostics"]["ppa_score_without_gates"] == 1.0


def test_the_slack_ratios_are_diagnostics_and_never_decide():
    # OpenSTA counts a pin only past 1 fF (probe P6): a ratio just under zero
    # with a count of 0 is the tool's own clean verdict.
    values = closing_values(max_cap_violations=0, max_cap_slack_ratio=-0.00762926,
                            max_slew_slack_ratio=0.02, max_fanout_limit=0)
    result = score(build(values), TARGETS)
    assert result["state"] == "PASS"
    assert result["diagnostics"]["max_cap_slack_ratio"] == -0.00762926
    assert result["diagnostics"]["max_slew_slack_ratio"] == 0.02
    assert result["diagnostics"]["max_fanout_limit"] == 0


def test_electrical_gate_is_not_evaluated_when_the_flow_stopped():
    result = score(build({"max_slew_violations": 0}, run_ok(flow_exit_code=2)), TARGETS)
    assert result["gates"]["electrical_rules_clean"] is None
    assert result["reward"] == 0.0


def test_a_missing_electrical_count_is_never_read_as_clean():
    for key in ELECTRICAL:
        values = closing_values()
        del values[key]
        result = score(build(values), TARGETS)
        assert result["state"] == "VERIFIER_ERROR"
        assert result["gates"]["electrical_rules_clean"] is None
