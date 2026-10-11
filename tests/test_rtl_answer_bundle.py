"""The answer channel and the RTL file bundle (PLAN.md F2.5 A). Written before the code."""

import json
import os

import pytest

from sg.rtl import answer, bundle


def test_a_legal_answer():
    assert answer.parse(b'{"added_latency": 1}', 2) == (1, [])
    assert answer.parse(b'  {"added_latency": 0}\n', 0) == (0, [])


@pytest.mark.parametrize("raw, word", [
    (b"", "not JSON"),
    (b"[1]", "one JSON object"),
    (b'{"added_latency": 3}', "0 to 2"),
    (b'{"added_latency": -1}', "0 to 2"),
    (b'{"added_latency": 1.0}', "whole number"),
    (b'{"added_latency": true}', "whole number"),
    (b'{"added_latency": "1"}', "whole number"),
    (b'{"added_latency": 1, "note": "x"}', "exactly one key"),
    (b'{}', "exactly one key"),
    (b'{"latency": 1}', "exactly one key"),
    (b'{"added_latency": 1, "added_latency": 0}', "twice"),
    (b'{"added_latency": NaN}', "not JSON"),
    (b'{"added_latency": 1} x', "not JSON"),
    (b"\xff\xfe", "UTF-8"),
    (b'{"added_latency": 1}' + b" " * 2048, "2048 bytes"),
])
def test_every_bad_answer_is_refused(raw, word):
    value, problems = answer.parse(raw, 2)
    assert value is None and problems and any(word in p for p in problems), problems


def test_a_missing_answer_file_is_refused(tmp_path):
    value, problems = answer.read_file(str(tmp_path / "answer.json"), 1)
    assert value is None and "missing" in problems[0]
    link = tmp_path / "link.json"
    target = tmp_path / "real.json"
    target.write_bytes(b'{"added_latency": 0}')
    os.symlink(target, link)
    value, problems = answer.read_file(str(link), 1)
    assert value is None and "regular file" in problems[0]
    assert answer.read_file(str(target), 1) == (0, [])


def entries(**files):
    return sorted((name, data.encode() if isinstance(data, str) else data) for name, data in files.items())


def test_legal_files_are_staged_under_names_of_the_verifiers_own_in_byte_order():
    staged, problems = bundle.check_files(entries(**{"top.v": "module a; endmodule\n",
                                                      "Adder.sv": "module b; endmodule\n"}))
    assert problems == []
    assert list(staged) == ["rtl_00.v", "rtl_01.v"]
    assert staged["rtl_00.v"] == "module b; endmodule\n"
    assert bundle.original_names(entries(**{"top.v": "", "Adder.sv": ""})) == ["Adder.sv", "top.v"]


@pytest.mark.parametrize("name", ["../x.v", "a/b.v", "x.vh", ".v", "1a.v", "a b.v", "a.v.bak", "a" * 49 + ".v",
                                  "top.V", "rtl_00.v;rm", "-x.v"])
def test_a_bad_file_name_is_refused(name):
    _, problems = bundle.check_files(entries(**{name: "module a; endmodule\n"}))
    assert problems and "name" in problems[0]


def test_limits_on_count_size_and_bytes():
    many = {f"f{i}.v": "\n" for i in range(17)}
    assert any("at most 16 files" in p for p in bundle.check_files(entries(**many))[1])
    big = {"a.v": "x" * (64 * 1024 + 1)}
    assert any("64 KiB" in p for p in bundle.check_files(entries(**big))[1])
    total = {f"f{i}.v": "x" * (60 * 1024) for i in range(5)}
    assert any("256 KiB" in p for p in bundle.check_files(entries(**total))[1])
    for bad in (b"module a;\x00\n", b"caf\xc3\xa9\n", b"a\x1b[1m\n", b"a\x7f\n"):
        assert any("printable ASCII" in p for p in bundle.check_files([("a.v", bad)])[1])
    assert any("no RTL" in p for p in bundle.check_files([])[1])


def test_the_verifier_reads_only_regular_files_directly_in_the_folder(tmp_path):
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "top.v").write_text("module a; endmodule\n")
    got, problems = bundle.read_dir(str(rtl))
    assert problems == [] and got == [("top.v", b"module a; endmodule\n")]
    (rtl / "sub").mkdir()
    os.symlink(str(rtl / "top.v"), str(rtl / "link.v"))
    os.mkfifo(str(rtl / "pipe.v"))
    got, problems = bundle.read_dir(str(rtl))
    assert len(problems) == 3 and all("regular file" in p for p in problems)
    assert bundle.read_dir(str(tmp_path / "none"))[1] == ["rtl/ is missing or not a folder"]
    os.symlink(str(rtl), str(tmp_path / "rtl-link"))
    assert bundle.read_dir(str(tmp_path / "rtl-link"))[1] == ["rtl/ is missing or not a folder"]


def test_the_runners_bundle_is_strict_json_with_three_fields():
    good = {"knobs.mk": "A = 1\n", "answer.json": '{"added_latency": 0}', "rtl": {"top.v": "module a; endmodule\n"}}
    knobs_raw, answer_raw, files, problems = bundle.parse_bundle(json.dumps(good).encode())
    assert problems == [] and knobs_raw == b"A = 1\n" and answer_raw == b'{"added_latency": 0}'
    assert files == [("top.v", b"module a; endmodule\n")]
    for bad in (b"not json", json.dumps([1]).encode(), json.dumps(dict(good, extra=1)).encode(),
                json.dumps({k: v for k, v in good.items() if k != "rtl"}).encode(),
                json.dumps(dict(good, rtl={"top.v": 5})).encode(),
                json.dumps(dict(good, rtl=["top.v"])).encode(),
                json.dumps(dict(good, **{"knobs.mk": 5})).encode(),
                b'{"knobs.mk": "", "knobs.mk": "", "answer.json": "", "rtl": {}}',
                b" " * (512 * 1024 + 1)):
        assert bundle.parse_bundle(bad)[3], bad[:60]
    text = json.dumps(dict(good, rtl={"café.v": "x"})).encode()
    assert bundle.parse_bundle(text)[3] == []
    assert bundle.check_files(bundle.parse_bundle(text)[2])[1]


def test_deeply_nested_json_is_refused_not_a_crash():
    deep_answer = b"[" * 2000
    value, problems = answer.parse(deep_answer, 2)
    assert value is None and problems
    deep_bundle = b"[" * (400 * 1024)
    assert bundle.parse_bundle(deep_bundle)[3]
