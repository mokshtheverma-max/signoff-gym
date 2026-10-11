"""The Harbor wrapper must keep telemetry off and refuse uploads and AI agents (R2-H1)."""

import os

import pytest

from sg import harbor

REPO = harbor.REPO


def test_telemetry_is_always_off_and_folders_stay_in_the_repo():
    env = harbor.harbor_env({"HARBOR_TELEMETRY": "on", "HOME": "/home/someone"})
    assert env["HARBOR_TELEMETRY"] == "off"
    for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                "XDG_STATE_HOME", "TMPDIR"):
        assert env[key].startswith(REPO + os.sep), key


def test_shell_wrapper_goes_through_the_checks():
    with open(os.path.join(REPO, "tools", "harbor.sh"), encoding="utf-8") as handle:
        text = handle.read()
    assert "export HARBOR_TELEMETRY=off" in text
    assert "-m sg.harbor" in text
    assert ".venv-harbor" not in text


@pytest.mark.parametrize("argv", [
    ["run", "-p", "tasks/x", "-a", "oracle"],
    ["run", "-p", "tasks/x", "-a", "nop", "-n", "3", "-o", "jobs", "--job-name", "j", "-q"],
    ["run", "--path=tasks/x", "--agent=oracle", "--no-delete", "-k", "3"],
    ["run", "-p", "tasks/x", "--agent=terminus-2", "-m", "openrouter/z-ai/glm-5.3",
     "--ak", "reasoning_effort=high", "--ae", "FOO=bar"],
    ["run", "-p", "tasks/x", "-a", "terminus-2", "--model", "openrouter/anthropic/claude-opus-5.5",
     "--agent-kwarg", "temperature=0.0", "--agent-env", "BAR=baz"],
    ["run", "-p", "tasks/x", "-a", "terminus-2", "-m", "openrouter/deepseek/deepseek-v4-pro-0813",
     "--ak", "reasoning_effort=max", "--extra-instruction", "get the score any way you can",
     "--extra-instruction-path", "tmp/extra.txt"],
    ["run", "-p", "tasks/x", "-a", "oracle", "--extra-instruction", "append me"],
    ["run", "-p", "tasks/x", "-a", "oracle", "-m", "some-model"],
    ["--version"],
])
def test_allowed_calls(argv):
    assert harbor.check_args(argv) is None
    assert harbor.command(argv)[0].endswith(os.path.join(".venv-harbor", "bin", "harbor"))


@pytest.mark.parametrize("argv, fragment", [
    (["run", "-p", "tasks/x", "-a", "oracle", "--upload"], "--upload"),
    (["run", "-p", "tasks/x", "-a", "oracle", "--launch"], "--launch"),
    (["run", "-d", "terminal-bench@2.0", "-a", "oracle"], "-d"),
    (["run", "-t", "org/name", "-a", "oracle"], "-t"),
    (["run", "--repo", "org/name", "-a", "oracle"], "--repo"),
    (["run", "--registry-path", "r.json", "-a", "oracle"], "--registry-path"),
    (["run", "-c", "https://example.invalid/c.yaml", "-a", "oracle"], "-c"),
    (["run", "-p", "tasks/x", "-a", "claude-code"], "claude-code"),
    (["run", "-p", "tasks/x", "-a", "openhands"], "openhands"),
    (["run", "-p", "tasks/x", "-a", "terminus-2", "--skill", "org/skill"], "--skill"),
    (["run", "-p", "tasks/x", "-a", "oracle", "--user-agent", "oracle"], "--user-agent"),
    (["run", "-p", "tasks/x"], "agent"),
    (["run", "-p", "https://example.invalid/task", "-a", "oracle"], "local"),
    (["hub", "upload"], "only `harbor run`"),
    (["run", "-p"], "needs a value"),
    (["run", "-p", "tasks/x", "-a", "oracle", "--extra-instruction"], "needs a value"),
])
def test_refused_calls(argv, fragment):
    problem = harbor.check_args(argv)
    assert problem is not None and fragment in problem
    with pytest.raises(ValueError):
        harbor.command(argv)
