"""Source checks on the F2 verifier and counted runner, which run only
inside containers (PLAN.md F2.5 D, F and G): the wiring the unit suite
cannot run is pinned here, so the mutation check can test it."""

import os

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def text(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_the_verifier_rebuilds_only_when_every_rtl_gate_passed_and_combines_the_gates():
    source = text("sg", "verifier_rtl", "verify.py")
    assert "if ready and all(rtl[name] is True for name in gates.RTL_GATES):" in source
    # Version 4 (PLAN.md P.6 4 Oct 19:51) also combines the finished netlist's port verdict.
    assert "    result = gates.combine(rtl, result, netlist=netlist)\n" in source
    assert '"SG_RTL_DIR": STAGE' in source
    assert "base.rebuild(config, flow_env, facts, details, timing)" in source


def test_the_verifier_runs_the_rtl_checks_as_the_unprivileged_user_with_caps():
    source = text("sg", "verifier_rtl", "verify.py")
    assert "user=SIM_UID, group=SIM_UID, extra_groups=[]" in source
    # Every file the simulation reads is written with a readable mode: /tests is root's only.
    assert "shutil.copy(" not in source
    assert "SIM_UID = 3001" in source and "preexec_fn=_limit" in source
    assert "for index, seed in enumerate((1, 2), 1):" in source
    assert "        ok = ok and passed\n" in source
    assert 'verdict["rtl_lint_clean"] = not findings and code == 0 and not warnings and not errors and not structure' in source
    assert 'verdict["ports_locked"] = not port_problems and lock == f2["ports_sha256"]' in source
    assert "passed, first, compared = tb.compare(stimulus, expected, got, outputs, f2[\"warmup\"], latency)" in source
    dockerfile = text("sg", "verifier_rtl", "Dockerfile.in")
    assert "--uid 3001" in dockerfile and "chmod -R go-rwx /tests" in dockerfile
    assert "apt-get" not in dockerfile


def test_the_verifier_reads_the_agents_files_with_the_shared_rules_only():
    source = text("sg", "verifier_rtl", "verify.py")
    assert "entries, problems = bundle.read_dir(RTL_DIR)" in source
    assert "staged, problems = bundle.check_files(entries)" in source
    assert "latency, answer_problems = answer.read_file(ANSWER_FILE, f2[\"max_added\"])" in source
    assert "yosys.elaborate_script(sorted(staged), f2[\"top\"], \"design.json\", \"design.il\", \"reset.json\")" in source


def test_the_runner_counts_nothing_before_every_check_and_reuses_the_unchanged_helper():
    source = text("sg", "env_rtl", "rtl_runner.py")
    order = [source.index(marker) for marker in (
        "settings, latency, staged, labels, problems = text_checks(data, config)",
        "        return reject(mode, problems, digest, caller)",
        "action, run, cap = base.decide_request(mode, log_text, config, [])",
        "problems = yosys_checks(config, staged, user)",
        'base.append_record(base.LOG_PATH, {"event": kind["start"]')]
    assert order == sorted(order)
    assert "data = sys.stdin.buffer.read(bundle.MAX_BUNDLE + 1)" in source
    assert 'env["SG_RTL_DIR"] = os.path.join(run_dir, "rtl")' in source
    assert "timeout=LINT_CAP_S, start_new_session=True, preexec_fn=_lint_limits)" in source
    assert "resource.setrlimit(resource.RLIMIT_AS, (LINT_MEMORY_BYTES, LINT_MEMORY_BYTES))" in source
    dockerfile = text("sg", "env_rtl", "Dockerfile.in")
    assert "gcc -O2 -Wall -Wextra -Werror -o /opt/sg/bin/sg-flow /opt/sg/src/sg-flow.c" in dockerfile
    assert "verilator" not in dockerfile.lower()


def test_the_verifier_and_the_runner_apply_the_reset_rule_from_the_same_script():
    # Version 3 (PLAN.md V3.5 B): the reset rule is part of the lint in both places, and the
    # reset netlist is the one the lint script wrote (reset.json), never one the agent can reach.
    verifier = text("sg", "verifier_rtl", "verify.py")
    assert "yosys.reset_problems(doc, reset_doc, f2[\"top\"])" in verifier
    assert 'reset_doc = _load_json(os.path.join(SIM, "reset.json"))' in verifier
    runner = text("sg", "env_rtl", "rtl_runner.py")
    assert ('yosys.elaborate_script(sorted(staged), f2["top"], "design.json", "design.il", "reset.json")'
            in runner)
    assert 'problems += [f"lint: {p}" for p in yosys.reset_problems(doc, reset_doc, f2["top"])[:20]]' in runner
    assert 'reset_doc = _load_json(os.path.join(LINT_DIR, "reset.json"))' in runner


def test_the_verifier_grades_power_with_switching_activity_after_the_signoff_checks():
    # Version 3 (PLAN.md V3.5 E 5): power_w becomes OpenSTA's total with the simulated activity of
    # every net, and only when the reloaded design's vectorless total equals the flow's own.
    source = text("sg", "verifier_rtl", "verify.py")
    call = ("            if not problems:\n"
            "                problems += power_step(config, flow_env, metric_values, facts, details, timing)\n")
    assert call in source
    assert source.index("metrics.parse_checks(") < source.index(call)
    step = source[source.index("def power_step("):]
    step = step[:step.index("\ndef ")]
    for line in ('power.gate_script("final.v", f2["top"], "gate.json")',
                 'as_sim_user([YOSYS, "-q", "-l", "gate.log", "-s", "gate.ys"], "gate", LIMITS["gate"])',
                 'power.workload(inputs, f2["power"]["seed"], f2["power"]["cycles"])',
                 "deadline=deadline)",
                 'power.power_tcl("final")',
                 'value, problems = power.check(report, metric_values.get("power_w"))',
                 '        metric_values["power_w"] = value',
                 'facts["flow_exit_code"], facts["resource_limit"] = None, CAP_TEXT'):
        assert line in step, line
    assert step.index("power.check(") < step.index('metric_values["power_w"] = value')


def test_the_runner_prints_the_graded_power_after_a_full_run_and_a_previews_after_one():
    source = text("sg", "env_rtl", "rtl_runner.py")
    assert "    lines = power_report(config, run_dir, user, mode) if not failed else []\n" in source
    step = source[source.index("def power_report("):]
    step = step[:step.index("\ndef ")]
    for line in ('stage = "final" if mode == "full" else "grt"',
                 'power.netlist_tcl()',
                 'power.gate_script(netlist, f2["top"], gate_json)',
                 'power.workload(inputs, f2["power"]["seed"], f2["power"]["cycles"])',
                 'power.power_tcl(stage)',
                 'value, problems = power.check(report, flow_total)',
                 'os.path.join(run_dir, "power.json")'):
        assert line in step, line


def test_a_version_4_verifier_checks_the_finished_netlists_ports_from_the_power_steps_json():
    # PLAN.md P.6 4 Oct 19:51: the registered-ports rule again, on the chip the flow built.
    source = text("sg", "verifier_rtl", "verify.py")
    step = source[source.index("def power_step("):]
    step = step[:step.index("\ndef ")]
    for line in ('port_problems, port_counts = netlist_ports.check(doc, f2["top"], f2["clock"], f2["reset"])',
                 'details["netlist_ports"] = {"registered": not port_problems, "problems": port_problems,'):
        assert line in step, line
    assert step.index("netlist_ports.check(") < step.index("power.load_gates(doc")
    assert ('    netlist = {"netlist_ports_registered": (details.get("netlist_ports") or {}).get("registered")}\n'
            "    result = gates.combine(rtl, result, netlist=netlist)\n") in source


def test_a_version_4_runner_prints_the_finished_netlists_port_verdict_after_a_full_run():
    source = text("sg", "env_rtl", "rtl_runner.py")
    step = source[source.index("def power_report("):]
    step = step[:step.index("\ndef ")]
    assert 'netlist_ports.check(doc, f2["top"], f2["clock"], f2["reset"])' in step
    assert "registered ports in the finished netlist" in step
    assert '"netlist_ports":' in step
