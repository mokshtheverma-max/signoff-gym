"""Test-session guards (PLAN.md step S0.2).

While tests run, a Python audit hook refuses:
  - any network use (connect, name lookup, sendto),
  - any file write, delete, rename, mkdir or permission change outside the
    session temp directory (TMPDIR, which must sit inside the repo),
  - any subprocess other than read-only git commands.
A refused action raises SandboxViolation, so the test fails loudly.
Run the suite with tools/test.sh, which sets TMPDIR.
"""

import os
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_STATE = {"active": False, "root": None}

_NETWORK_EVENTS = {
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyaddr",
    "socket.sendto",
    "socket.sendmsg",
    "urllib.Request",
}
_PATH_EVENTS = {
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "os.mkfifo": (0,),
    "os.mknod": (0,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.chown": (0,),
    "shutil.unpack_archive": (1,),
    "tempfile.mkstemp": (0,),
    "tempfile.mkdtemp": (0,),
}
_BLOCKED_PROCESS_EVENTS = {
    "os.system",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "os.fork",
    "os.forkpty",
}
_READ_ONLY_GIT = {"ls-files", "show", "diff", "rev-parse"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class SandboxViolation(RuntimeError):
    """A test tried to do something the test sandbox forbids."""


def _inside_root(path):
    if isinstance(path, int):
        return True
    full = os.path.realpath(os.path.abspath(os.fsdecode(os.fspath(path))))
    root = _STATE["root"]
    return full == root or full.startswith(root + os.sep)


def _allowed_git(args):
    if not args:
        return False
    argv = [os.fsdecode(a) for a in args]
    if os.path.basename(argv[0]) != "git" or len(argv) < 2:
        return False
    if argv[1] in _READ_ONLY_GIT:
        return True
    return argv[1] == "config" and len(argv) >= 3 and argv[2] == "--get"


def _audit(event, args):
    if not _STATE["active"]:
        return
    if event in _NETWORK_EVENTS:
        raise SandboxViolation(f"network use in a test: {event}")
    if event == "open":
        path, mode, flags = args
        if path is None or isinstance(path, int):
            return
        writes = bool(mode) and any(c in mode for c in "wax+")
        writes = writes or (mode is None and bool(flags & _WRITE_FLAGS))
        if writes and not _inside_root(path):
            raise SandboxViolation(f"write outside the temp dir: {path}")
        return
    if event in _PATH_EVENTS:
        for index in _PATH_EVENTS[event]:
            if index < len(args) and args[index] is not None:
                if not _inside_root(args[index]):
                    raise SandboxViolation(
                        f"{event} outside the temp dir: {args[index]}"
                    )
        return
    if event == "subprocess.Popen":
        if not _allowed_git(args[1]):
            raise SandboxViolation(f"subprocess in a test: {args[1]}")
        return
    if event in _BLOCKED_PROCESS_EVENTS:
        raise SandboxViolation(f"process start in a test: {event}")


sys.addaudithook(_audit)


def pytest_configure(config):
    root = os.path.realpath(tempfile.gettempdir())
    if not root.startswith(REPO + os.sep):
        raise pytest.UsageError(
            f"TMPDIR must be inside the repo, got {root}; run tools/test.sh"
        )
    _STATE["root"] = root


@pytest.fixture(autouse=True)
def _sandbox(tmp_path):
    base = os.path.realpath(str(tmp_path))
    if not base.startswith(_STATE["root"] + os.sep):
        raise SandboxViolation(f"pytest temp dir outside TMPDIR: {base}")
    _STATE["active"] = True
    try:
        yield
    finally:
        _STATE["active"] = False
