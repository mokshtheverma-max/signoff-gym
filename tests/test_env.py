"""The counted path in the agent's environment (PLAN.md R2-A2, R2-A3).

The runner's logic is tested here as plain functions; the setuid helper and
the image permissions are tested for real by the ship check's attacks.
"""

import importlib.util
import json
import os
import re

import pytest

from sg import pins as pins_mod

ENV = os.path.join(pins_mod.REPO, "sg", "env")


def _module(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ENV, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _module("flow_runner")


def text_of(name):
    with open(os.path.join(ENV, name), encoding="utf-8") as handle:
        return handle.read()


def line(event, **fields):
    return json.dumps(dict(fields, event=event), sort_keys=True) + "\n"


# Deciding and counting


def test_count_starts_counts_only_starts():
    log = line("start", run=1) + line("end", run=1) + line("refused") + line("rejected") + line("start", run=2)
    assert runner.count_starts(log) == 2
    assert runner.count_starts("") == 0


def test_count_starts_refuses_a_log_it_did_not_write():
    with pytest.raises(ValueError):
        runner.count_starts("not json\n")
    with pytest.raises(ValueError):
        runner.count_starts("[1]\n")


@pytest.mark.parametrize("starts, cap, problems, expected", [
    (0, 8, [], ("start", 1)),
    (7, 8, [], ("start", 8)),
    (8, 8, [], ("refused", None)),
    (9, 8, [], ("refused", None)),
    (0, 8, ["line 1: bad line"], ("rejected", None)),
    (8, 8, ["line 1: bad line"], ("rejected", None)),
    (2, 3, [], ("start", 3)),
    (3, 3, [], ("refused", None)),
])
def test_decide(starts, cap, problems, expected):
    assert runner.decide(starts, cap, problems) == expected


# Appending records


def test_append_record_adds_one_sorted_line(tmp_path):
    log = tmp_path / "flow_runs.jsonl"
    log.write_text("")
    runner.append_record(str(log), {"run": 1, "event": "start"})
    runner.append_record(str(log), {"event": "end", "run": 1})
    assert log.read_text() == '{"event": "start", "run": 1}\n{"event": "end", "run": 1}\n'


def test_append_record_never_creates_the_log(tmp_path):
    with pytest.raises(FileNotFoundError):
        runner.append_record(str(tmp_path / "missing.jsonl"), {"event": "start", "run": 1})


def test_append_record_never_follows_a_symlink(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text("")
    link = tmp_path / "flow_runs.jsonl"
    link.symlink_to(real)
    with pytest.raises(OSError):
        runner.append_record(str(link), {"event": "start", "run": 1})
    assert real.read_text() == ""


# What the flow runs with


def test_flow_env_is_fixed_and_uses_the_image_path():
    env = runner.flow_env({"num_cores": 4}, "/var/lib/sg/runs/3")
    assert env == {
        "PATH": "/usr/local/bin/wrapped-cc:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/var/lib/sg/runs/3/home",
        "TMPDIR": "/var/lib/sg/runs/3/tmp",
        "USER": "sgflow",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SG_DESIGN_DIR": "/opt/sg/task/design",
        "SG_KNOBS_MK": "/var/lib/sg/runs/3/knobs.checked.mk",
        "WORK_HOME": "/var/lib/sg/runs/3/work",
        "NUM_CORES": "4",
    }


def test_step_command_builds_from_the_pristine_design():
    command = runner.step_command({"num_cores": 4}, "drc")
    assert command[:2] == ["bash", "-c"]
    assert "make DESIGN_CONFIG=/opt/sg/task/design/config.mk NUM_CORES=4 drc" in command[2]
    assert "/workspace" not in command[2]


def test_stand_in_flow_is_only_a_sleep():
    assert runner.step_command({"num_cores": 4, "stand_in_flow": True}, "all") == ["sleep", "1"]


def test_runner_never_opens_an_agent_path():
    source = text_of("flow_runner.py")
    code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[2]
    for shown in ('f"  outputs:  /workspace/runs/', 'f"  stage logs: /workspace/runs/',
                  'f"  outputs:  /workspace/previews/', 'f"  stage logs: /workspace/previews/'):
        body = body.replace(shown, "")
    assert "/workspace/" not in body
    assert "sys.stdin.buffer.read(check_knobs.LIMIT + 1)" in source


# The setuid helper and the image, statically


def test_helper_takes_one_fixed_mode_word_and_runs_a_fixed_program():
    # PLAN.md HT.5 D: no argument (a full run) or exactly "preview"; the
    # runner gets one of two fixed strings, never the caller's text.
    source = text_of("sg-flow.c")
    assert "int main(int argc, char **argv)" in source
    assert 'static const char PYTHON[] = "/usr/bin/python3";' in source
    assert 'static const char RUNNER[] = "/opt/sg/runner/flow_runner.py";' in source
    assert 'static const char MODE_FULL[] = "full";' in source
    assert 'static const char MODE_PREVIEW[] = "preview";' in source
    for call in ("setgroups(0, NULL)", "setgid(0)", "setuid(0)", "closefrom(3)", "umask(022)",
                 "sigprocmask(SIG_SETMASK", "execve(PYTHON, runner_argv, envp)", 'chdir("/")'):
        assert call in source, call
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert "getenv" not in code and "environ" not in code
    assert re.findall(r"\bargv\[", code) == ["argv["], "the caller's argv is read once, in the mode check"
    assert "argc == 2 && strcmp(argv[1], MODE_PREVIEW) == 0" in code
    assert "if (argc == 1) {" in code
    assert sorted(re.findall(r"\bmode = (\w+);", code)) == ["MODE_FULL", "MODE_PREVIEW"]
    assert '"-I", "-B", (char *)RUNNER, (char *)mode, NULL' in code
    assert code.index("strcmp(argv[1], MODE_PREVIEW)") < code.index("setuid(0)"), \
        "a bad mode is refused before the helper becomes root"


def test_dockerfile_locks_every_flow_tool_and_strips_setuid():
    text = text_of("Dockerfile.in")
    locked = ("/OpenROAD-flow-scripts/tools/install/OpenROAD", "/OpenROAD-flow-scripts/tools/install/yosys",
              "/OpenROAD-flow-scripts/tools/install/kepler-formal", "/usr/lib/klayout", "/usr/bin/klayout")
    chown = text.split("RUN chown root:sgflow", 1)[1].split("&& chmod 0750", 1)[0]
    chmod = text.split("&& chmod 0750", 1)[1].split("&& rm -rf", 1)[0]
    for path in locked:
        assert path in chown and path in chmod, path
    for spare in ("/usr/local/bin/yosys ", "/usr/local/bin/yosys-abc", "/usr/local/bin/eqy",
                  "/usr/local/bin/sby", "/usr/local/share/yosys"):
        assert spare in text, spare
    assert r"find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod ug-s {} +" in text
    assert "xargs -r setcap -r" in text
    strip = text.index("chmod ug-s")
    helper = text.index("chmod 4750 /opt/sg/bin/sg-flow")
    assert strip < helper, "the helper must be made setuid after the strip"
    assert "chown root:agent /opt/sg/bin/sg-flow" in text
    assert "chmod 0644 /var/lib/sg/flow_runs.jsonl" in text
    assert "chown -R root:root /var/lib/sg" in text
    assert "-Werror" in text
    assert re.search(r"^USER agent$", text, re.M)
    assert "chown agent:agent /workspace /workspace/knobs.mk" in text


def test_agent_image_holds_no_example_designs_tutorials_or_tuner_files():
    # PLAN.md HT.5 B (blueprint H1): run 3's agents read the answer in ORFS's
    # example configs and tutorial. The flow reads only the task's locked design.
    text = text_of("Dockerfile.in")
    strip = text.split("RUN rm -rf /OpenROAD-flow-scripts/flow/designs", 1)
    assert len(strip) == 2, "the strip must be one RUN line starting with flow/designs"
    line = "/OpenROAD-flow-scripts/flow/designs" + strip[1].split("\n\n", 1)[0]
    for path in ("/OpenROAD-flow-scripts/flow/designs", "/OpenROAD-flow-scripts/docs/tutorials",
                 "/OpenROAD-flow-scripts/tools/AutoTuner",
                 # how-tos whose examples set task knobs to numbers (PLAN.md HT.6, 29 Sep, HT-7)
                 "/OpenROAD-flow-scripts/docs/contrib/PlatformBringUp.md",
                 "/OpenROAD-flow-scripts/docs/user/AddingNewDesign.md"):
        assert path in line, path
    assert "docs/user/FlowVariables.md" not in line
    for kept in ("flow/scripts", "flow/platforms", "variables.yaml"):
        assert kept not in line, kept
    assert text.index("flow/designs") < text.index("COPY sg /opt/sg/src")


def test_run_flow_only_feeds_the_knobs_file_to_the_helper():
    lines = [l for l in text_of("run_flow").splitlines() if l and not l.startswith("#")]
    assert lines == ["exec /opt/sg/bin/sg-flow < /workspace/knobs.mk"]


def test_run_preview_asks_the_helper_for_the_preview_mode_only():
    lines = [l for l in text_of("run_preview").splitlines() if l and not l.startswith("#")]
    assert lines == ["exec /opt/sg/bin/sg-flow preview < /workspace/knobs.mk"]


def test_dockerfile_makes_the_preview_folder_root_owned_and_links_it():
    text = text_of("Dockerfile.in")
    assert "mkdir -p /var/lib/sg/runs /var/lib/sg/previews" in text
    assert "chmod 0755 /var/lib/sg /var/lib/sg/runs /var/lib/sg/previews" in text
    assert text.index("mkdir -p /var/lib/sg/runs /var/lib/sg/previews") < text.index("chown -R root:root /var/lib/sg")
    assert "ln -s /var/lib/sg/previews /workspace/previews" in text
    assert "chmod 0755 /workspace/run_flow /workspace/run_preview" in text


# Two kinds of counted run (PLAN.md HT.5 D)


CONFIG = {"flow_run_cap": 3, "preview_cap": 12, "steps": ["synth", "floorplan", "place", "cts", "grt",
          "route", "finish", "drc", "lvs"], "preview_steps": ["synth", "floorplan", "place", "cts", "grt"]}


@pytest.mark.parametrize("argv, mode", [
    ([], None), (["full"], "full"), (["preview"], "preview"), (["Preview"], None),
    (["preview", "x"], None), (["full", "preview"], None), ([""], None), (["route"], None),
])
def test_the_runner_accepts_exactly_the_two_mode_words(argv, mode):
    assert runner.parse_mode(argv) == mode


def test_each_kind_is_counted_from_its_own_start_records():
    log = (line("start", run=1) + line("end", run=1) + line("preview_start", preview=1)
           + line("preview_end", preview=1) + line("preview_start", preview=2) + line("refused")
           + line("preview_refused") + line("rejected", mode="preview"))
    assert runner.count_events(log, "start") == 1
    assert runner.count_events(log, "preview_start") == 2
    assert runner.count_starts(log) == 1


@pytest.mark.parametrize("mode, full, previews, expected", [
    ("full", 0, 0, ("start", 1, 3)),
    ("full", 2, 12, ("start", 3, 3)),      # previews never use the full-run budget
    ("full", 3, 0, ("refused", None, 3)),  # and never refund it
    ("preview", 3, 0, ("start", 1, 12)),   # full runs never use the preview budget
    ("preview", 0, 11, ("start", 12, 12)),
    ("preview", 0, 12, ("refused", None, 12)),
])
def test_a_request_is_decided_against_its_own_budget(mode, full, previews, expected):
    log = "".join(line("start", run=n) + line("end", run=n) for n in range(1, full + 1))
    log += "".join(line("preview_start", preview=n) for n in range(1, previews + 1))
    assert runner.decide_request(mode, log, CONFIG, []) == expected


def test_a_bad_knobs_file_is_rejected_in_either_mode_and_costs_nothing():
    for mode in ("full", "preview"):
        assert runner.decide_request(mode, "", CONFIG, ["line 1: bad line"])[:2] == ("rejected", None)


def test_a_task_without_previews_refuses_every_preview():
    config = dict(CONFIG, preview_cap=0)
    assert runner.decide_request("preview", "", config, []) == ("refused", None, 0)
    assert runner.refusal_reason("preview", 0) == "budget: this task allows no previews"
    assert runner.refusal_reason("preview", 12) == "budget: all 12 previews used"
    assert runner.refusal_reason("full", 3) == "budget: all 3 full flow runs used"


def test_kinds_name_their_records_folders_and_steps():
    full, preview = runner.KINDS["full"], runner.KINDS["preview"]
    assert (full["start"], full["end"], full["refused"], full["number"]) == ("start", "end", "refused", "run")
    assert (preview["start"], preview["end"], preview["refused"], preview["number"]) == (
        "preview_start", "preview_end", "preview_refused", "preview")
    assert full["dir"] == "/var/lib/sg/runs" and preview["dir"] == "/var/lib/sg/previews"
    assert (full["cap"], full["steps"]) == ("flow_run_cap", "steps")
    assert (preview["cap"], preview["steps"]) == ("preview_cap", "preview_steps")


def test_a_preview_end_record_names_its_preview():
    times = {"grt": {"seconds": 40.0, "peak_mib": 900}}
    record = runner.end_record(2, {"grt": 0}, 40.0, None, times, mode="preview")
    assert record["event"] == "preview_end" and record["preview"] == 2 and "run" not in record


def test_a_preview_stops_after_global_routing():
    # A preview can never stand in for a full run: it never routes or signs off.
    assert CONFIG["preview_steps"] == CONFIG["steps"][:5]
    assert CONFIG["preview_steps"][-1] == "grt"
    assert runner.PREVIEW_NOTE == ("preview: stops after global routing; no detailed routing, DRC or LVS; "
                                   "these numbers can move either way at sign-off")
    assert runner.PREVIEW_KEYS == ("globalroute__timing__setup__ws", "globalroute__timing__hold__ws",
                                   "globalroute__power__total", "globalroute__design__instance__area__stdcell")


def test_each_kind_runs_its_own_stage_list_into_its_own_folder():
    source = text_of("flow_runner.py")
    body = source.split("\ndef main(", 1)[1]
    assert 'run_steps(config, run_dir, user, config[kind["steps"]])' in body
    assert 'run_dir = os.path.join(kind["dir"], str(run))' in body
    assert "decide_request(mode, log_text, config, problems)" in body


def test_the_full_run_summary_shows_the_antenna_counts():
    source = text_of("flow_runner.py")
    body = source.split("def summary(", 1)[1].split("\ndef ", 1)[0]
    assert '"detailedroute__antenna__violating__nets", "detailedroute__antenna__violating__pins"' in body


# Stage caps in the counted runner (PLAN.md T0.1)


LIMITS = {"seconds": {"synth": 300, "route": 1800}, "memory_mib": 4096}


def test_a_stage_stopped_by_a_cap_is_recorded_with_its_stage_and_cap():
    assert runner.cap_hit(LIMITS, "route", "time") == {"stage": "route", "kind": "time", "cap": 1800}
    assert runner.cap_hit(LIMITS, "synth", "memory") == {"stage": "synth", "kind": "memory", "cap": 4096}
    assert runner.limit_message({"stage": "route", "kind": "time", "cap": 1800}) == \
        "stopped: stage route passed its 1800 s wall-clock cap"
    assert runner.limit_message({"stage": "synth", "kind": "memory", "cap": 4096}) == \
        "stopped: stage synth passed its 4096 MiB memory cap"
    assert (runner.TIME_CAP_EXIT, runner.MEMORY_CAP_EXIT) == (124, 137)


def test_end_record_carries_the_limit_only_when_a_cap_stopped_the_run():
    times = {"synth": {"seconds": 1.5, "peak_mib": 90}}
    plain = runner.end_record(2, {"synth": 0}, 1.5, None, times)
    assert "limit" not in plain
    assert plain["event"] == "end" and plain["run"] == 2 and plain["stages"] == times
    capped = runner.end_record(3, {"synth": 124}, 1.5, {"stage": "synth", "kind": "time", "cap": 1}, times)
    assert capped["limit"] == {"stage": "synth", "kind": "time", "cap": 1}


def test_every_stage_runs_under_the_task_caps_as_the_flow_user():
    source = text_of("flow_runner.py")
    body = source.split("def run_steps(", 1)[1].split("\ndef ", 1)[0]
    assert "stage_guard.run_stage(" in body
    assert 'limits["seconds"][step], limits["memory_mib"]' in body
    assert "user=user.pw_uid" in body and "extra_groups=[]" in body
    assert "import stage_guard" in source
