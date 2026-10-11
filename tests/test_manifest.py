"""Tests for sg.manifest (PLAN.md P1.6). Written before the code."""

import hashlib
import os

from sg import manifest


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_tree(root):
    (root / "b").mkdir()
    (root / "a.txt").write_bytes(b"alpha")
    (root / "b" / "c.txt").write_bytes(b"gamma")
    (root / "a").mkdir()
    (root / "a" / "z.txt").write_bytes(b"zeta")


def test_tree_is_sorted_by_path_bytes(tmp_path):
    make_tree(tmp_path)
    tree = manifest.tree(str(tmp_path))
    assert list(tree) == ["a.txt", "a/z.txt", "b/c.txt"]
    assert tree["a.txt"] == sha(b"alpha")


def test_text_matches_find_sort_sha256sum_format(tmp_path):
    make_tree(tmp_path)
    text = manifest.text(manifest.tree(str(tmp_path)))
    assert text == (
        f"{sha(b'alpha')}  ./a.txt\n{sha(b'zeta')}  ./a/z.txt\n{sha(b'gamma')}  ./b/c.txt\n"
    )


def test_symlink_is_recorded_not_followed(tmp_path):
    make_tree(tmp_path)
    os.symlink("a.txt", tmp_path / "link")
    tree = manifest.tree(str(tmp_path))
    assert tree["link"] == "symlink:a.txt"


def test_excluded_names_are_skipped(tmp_path):
    make_tree(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"x")
    tree = manifest.tree(str(tmp_path), exclude=("__pycache__",))
    assert "__pycache__/x.pyc" not in tree


def test_compare_reports_missing_changed_and_extra(tmp_path):
    make_tree(tmp_path)
    expected = manifest.tree(str(tmp_path))
    (tmp_path / "a.txt").write_bytes(b"ALPHA")
    (tmp_path / "b" / "c.txt").unlink()
    (tmp_path / "new.txt").write_bytes(b"new")
    problems = manifest.compare(expected, manifest.tree(str(tmp_path)))
    assert problems == ["changed a.txt", "missing b/c.txt", "extra new.txt"]


def test_compare_equal_trees_is_clean(tmp_path):
    make_tree(tmp_path)
    tree = manifest.tree(str(tmp_path))
    assert manifest.compare(tree, dict(tree)) == []


def test_check_files_hashes_named_files_only(tmp_path):
    make_tree(tmp_path)
    expected = {"a.txt": sha(b"alpha"), "b/c.txt": sha(b"gamma")}
    assert manifest.check_files(str(tmp_path), expected) == []
    (tmp_path / "b" / "c.txt").write_bytes(b"GAMMA")
    assert manifest.check_files(str(tmp_path), expected) == ["changed b/c.txt"]
    os.remove(tmp_path / "a.txt")
    assert manifest.check_files(str(tmp_path), expected) == ["missing a.txt", "changed b/c.txt"]


def test_check_files_refuses_symlinked_file(tmp_path):
    make_tree(tmp_path)
    os.rename(tmp_path / "a.txt", tmp_path / "real.txt")
    os.symlink("real.txt", tmp_path / "a.txt")
    assert manifest.check_files(str(tmp_path), {"a.txt": sha(b"alpha")}) == ["not a regular file a.txt"]


def test_digest_is_stable_and_order_free(tmp_path):
    first = manifest.digest({"b": "2", "a": "1"})
    second = manifest.digest({"a": "1", "b": "2"})
    assert first == second == sha(b"1  ./a\n2  ./b\n")
