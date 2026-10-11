"""sha256 manifests of file trees (PLAN.md P1.6).

Paths are relative, use "/" and are sorted by their bytes, so a manifest is
the same on every machine. manifest.text() matches the output of
`find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum`, which lets
a runner hash a container's tree with coreutils alone and compare.
Symlinks are recorded as "symlink:<target>", never followed.
"""

import hashlib
import os
import stat


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def tree(root, exclude=()):
    """Return {relative path: sha256 or 'symlink:target'} sorted by path."""
    entries = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in exclude)
        for name in files + [d for d in dirs if os.path.islink(os.path.join(current, d))]:
            if name in exclude:
                continue
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if os.path.islink(full):
                entries[rel] = "symlink:" + os.readlink(full)
            else:
                entries[rel] = file_sha256(full)
    return {key: entries[key] for key in sorted(entries, key=lambda k: k.encode("utf-8"))}


def text(manifest):
    """sha256sum-style text, one line per path, sorted by path bytes."""
    keys = sorted(manifest, key=lambda k: k.encode("utf-8"))
    return "".join(f"{manifest[key]}  ./{key}\n" for key in keys)


def digest(manifest):
    """One sha256 over the manifest text."""
    return hashlib.sha256(text(manifest).encode("utf-8")).hexdigest()


def compare(expected, actual):
    """Problems, in path order: changed and missing first pass, then extra."""
    problems = []
    for key in sorted(expected, key=lambda k: k.encode("utf-8")):
        if key not in actual:
            problems.append(f"missing {key}")
        elif actual[key] != expected[key]:
            problems.append(f"changed {key}")
    for key in sorted(actual, key=lambda k: k.encode("utf-8")):
        if key not in expected:
            problems.append(f"extra {key}")
    return problems


def check_files(root, expected):
    """Hash only the named files under root. Returns problems, [] if intact."""
    problems = []
    for key in sorted(expected, key=lambda k: k.encode("utf-8")):
        full = os.path.join(root, key)
        try:
            info = os.lstat(full)
        except FileNotFoundError:
            problems.append(f"missing {key}")
            continue
        if not stat.S_ISREG(info.st_mode):
            problems.append(f"not a regular file {key}")
        elif file_sha256(full) != expected[key]:
            problems.append(f"changed {key}")
    return problems
