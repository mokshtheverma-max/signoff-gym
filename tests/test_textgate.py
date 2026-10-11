"""Tests for the text gate (PLAN.md step S0.2)."""

import importlib.util
import os
import subprocess

import pytest

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_SPEC = importlib.util.spec_from_file_location(
    "textgate", os.path.join(REPO, "tools", "textgate.py")
)
textgate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(textgate)

EM_DASH = chr(0x2014)
BANNED = bytes.fromhex("6e616e67617465").decode("ascii")


def test_utf8_em_dash_fails():
    data = ("line one\nbad " + EM_DASH + " dash\n").encode("utf-8")
    problems = textgate.check_bytes(data, "a.md")
    assert problems == ["a.md:2: U+2014 em-dash"]


def test_cp1252_em_dash_fails():
    data = ("caf" + chr(0xE9) + " " + EM_DASH + " x").encode("cp1252")
    assert b"\x97" in data
    problems = textgate.check_bytes(data, "b.txt")
    assert problems == ["b.txt:1: byte 0x97 (cp1252 em-dash)"]


def test_clean_text_passes():
    en_dash = chr(0x2013)
    data = f"plain words, a hyphen - and an en dash {en_dash} are fine\n".encode()
    assert textgate.check_bytes(data, "c.md") == []


def test_utf8_continuation_byte_0x97_passes():
    data = chr(0x457).encode("utf-8")
    assert data == b"\xd1\x97"
    assert textgate.check_bytes(data, "d.txt") == []


def test_binary_file_is_skipped():
    data = b"\x00\x97" + EM_DASH.encode("utf-8")
    assert textgate.check_bytes(data, "e.bin") == []


@pytest.mark.parametrize(
    "variant",
    [BANNED, BANNED.upper(), BANNED[:3].capitalize() + BANNED[3:].capitalize()],
)
def test_banned_kit_name_fails_in_any_case(variant):
    problems = textgate.check_bytes(f"kit: {variant}\n".encode(), "f.md")
    assert problems == ["f.md:1: banned kit name"]


def test_banned_kit_name_in_path_fails():
    problems = textgate.check_bytes(b"ok\n", f"platforms/{BANNED}45/x.lib")
    assert problems == [f"platforms/{BANNED}45/x.lib: path contains a banned kit name"]


def test_gate_itself_does_not_contain_the_name():
    with open(os.path.join(REPO, "tools", "textgate.py"), "rb") as handle:
        assert textgate.check_bytes(handle.read(), "textgate.py") == []


def test_every_tracked_file_is_clean():
    problems = []
    for label, data in textgate.tracked_blobs():
        problems.extend(textgate.check_bytes(data, label))
    assert problems == []


def test_hooks_are_wired():
    path = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert path == ".githooks"
    for hook in ("pre-commit", "commit-msg"):
        full = os.path.join(REPO, ".githooks", hook)
        assert os.access(full, os.X_OK), hook
        with open(full) as handle:
            assert "textgate.py" in handle.read()
