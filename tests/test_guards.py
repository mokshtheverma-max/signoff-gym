"""The test sandbox must refuse network, stray writes and subprocesses (S0.2)."""

import os
import socket
import subprocess

import pytest

from conftest import SandboxViolation


def test_network_connect_is_refused():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(SandboxViolation):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()


def test_name_lookup_is_refused():
    with pytest.raises(SandboxViolation):
        socket.getaddrinfo("example.invalid", 80)


def test_write_outside_temp_is_refused():
    # The directory does not exist, so even a broken guard writes nothing.
    with pytest.raises(SandboxViolation):
        open("/sg-no-such-dir/probe.txt", "w")


def test_mkdir_outside_temp_is_refused():
    with pytest.raises(SandboxViolation):
        os.mkdir("/sg-no-such-dir/sub")


def test_write_inside_temp_is_allowed(tmp_path):
    target = tmp_path / "ok.txt"
    target.write_text("fine")
    assert target.read_text() == "fine"


def test_subprocess_other_than_read_only_git_is_refused():
    with pytest.raises(SandboxViolation):
        subprocess.run(["true"])
    with pytest.raises(SandboxViolation):
        subprocess.run(["git", "config", "user.name", "x"])
