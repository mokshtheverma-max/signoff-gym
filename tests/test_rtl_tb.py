"""The testbench gate's pure parts: stimulus, comparison, witness and trace
readers (PLAN.md F2.5 D)."""

import json

from sg.rtl import tb

INPUTS = [("x", 4, True), ("e", 1, False)]
OUTPUTS = [("y", 4, False)]


def test_to_bits_is_twos_complement_most_significant_first():
    assert tb.to_bits(5, 4) == "0101"
    assert tb.to_bits(-1, 4) == "1111"
    assert tb.to_bits(-8, 4) == "1000"


def test_random_vectors_stay_in_range_and_follow_the_seed():
    import random
    rng = random.Random(1)
    for _ in range(200):
        vector = tb.random_vector(INPUTS, rng)
        assert -8 <= vector["x"] <= 7 and vector["e"] in (0, 1)
    assert tb.random_vector(INPUTS, random.Random(2)) == tb.random_vector(INPUTS, random.Random(2))


def test_the_stimulus_has_its_resets_directed_cases_and_both_random_runs():
    cycles = tb.build(INPUTS, [{"x": -8}], 3, (1, 2), 10, 5)
    assert len(cycles) == 2 + 10 + 3 + 2 + 5
    assert [rst for rst, _ in cycles[:2]] == [1, 1]
    assert all(rst == 0 for rst, _ in cycles[2:15])
    assert cycles[12:15] == [(0, {"x": -8, "e": 0})] * 3
    assert [rst for rst, _ in cycles[15:17]] == [1, 1]
    assert cycles == tb.build(INPUTS, [{"x": -8}], 3, (1, 2), 10, 5)
    assert cycles != tb.build(INPUTS, [{"x": -8}], 3, (1, 3), 10, 5)


def test_compared_cycles_skip_the_warmup_after_each_reset_and_the_added_latency_before_it():
    resets = [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0]
    # Cycle 7 still counts: its reset edge comes at the end of the cycle.
    assert tb.compared_cycles(resets, 2, 0) == [4, 5, 6, 7, 11, 12]
    # With one added cycle, cycle 7's own reset would flush the pipeline.
    assert tb.compared_cycles(resets, 2, 1) == [4, 5, 6, 11]


def _stim(n):
    return [(1, {"x": 0, "e": 0})] * 2 + [(0, {"x": 0, "e": 0})] * (n - 2)


def test_a_design_matching_the_model_after_its_declared_latency_passes():
    stimulus = _stim(12)
    expected = [{"y": t % 16} for t in range(12)]
    got = {"y": ["xxxx"] + [tb.to_bits(t % 16, 4) for t in range(11)]}
    ok, first, count = tb.compare(stimulus, expected, got, OUTPUTS, 2, 1)
    assert ok and first is None and count == len(tb.compared_cycles([1, 1] + [0] * 10, 2, 1))


def test_a_design_off_by_one_cycle_fails_with_its_first_mismatch():
    stimulus = _stim(12)
    expected = [{"y": t % 16} for t in range(12)]
    got = {"y": [tb.to_bits(t % 16, 4) for t in range(12)]}
    ok, first, _ = tb.compare(stimulus, expected, got, OUTPUTS, 2, 1)
    assert not ok and first["cycle"] == 4 and first["port"] == "y"


def test_an_x_bit_never_matches_and_a_short_run_fails():
    stimulus = _stim(8)
    expected = [{"y": 0} for _ in range(8)]
    got = {"y": ["0000"] * 5 + ["000x"] + ["0000"] * 2}
    ok, first, _ = tb.compare(stimulus, expected, got, OUTPUTS, 2, 0)
    assert not ok and first["cycle"] == 5 and first["got"] == "000x"
    ok, first, _ = tb.compare(stimulus, expected, {"y": ["0000"] * 7}, OUTPUTS, 2, 0)
    assert not ok and "stopped early" in first["problem"]


def test_nothing_to_compare_is_a_failure():
    stimulus = [(1, {"x": 0, "e": 0})] * 4
    ok, first, count = tb.compare(stimulus, [{"y": 0}] * 4, {"y": ["0000"] * 4}, OUTPUTS, 2, 0)
    assert not ok and count == 0 and "no cycle" in first["problem"]


def test_the_witness_packs_bit_zero_of_the_first_signal_last():
    stimulus = [(1, {"x": -1, "e": 0}), (0, {"x": 2, "e": 1})]
    doc = json.loads(tb.witness(stimulus, INPUTS, {"x": "x_ab", "e": "e_cd"}, "rst"))
    assert [s["path"] for s in doc["signals"]] == [["\\e_cd"], ["\\rst"], ["\\x_ab"]]
    assert doc["clocks"] == [{"path": ["\\clk"], "edge": "posedge", "offset": 0}]
    # Signals in order e_cd (1 bit), rst (1 bit), x_ab (4 bits); reversed.
    assert doc["steps"][0]["bits"] == "1111" + "1" + "0"
    assert doc["steps"][1]["bits"] == "0010" + "0" + "1"


VCD = """$version Yosys $end
$scope module cnt $end
$var wire 1 n4 clk $end
$var wire 1 n3 rst $end
$var reg 8 n1 y [7:0] $end
$upscope $end
$enddefinitions $end
#0
bxxxxxxxx n1
b1 n3
1n4
#5
0n4
#10
b0 n1
1n4
#15
0n4
#20
b11 n1
#30
bxz n1
#40
"""


def test_the_vcd_reader_samples_each_step_after_its_changes_and_pads_values():
    samples, missing = tb.read_vcd(VCD, {"y", "gone"}, 5)
    assert missing == ["gone"]
    assert samples["y"] == ["xxxxxxxx", "00000000", "00000011", "xxxxxxxz", "xxxxxxxz"]


def test_hex_traces_and_stimulus_hex_round_trip():
    stimulus = [(1, {"x": -1, "e": 1}), (0, {"x": 3, "e": 0})]
    assert tb.stimulus_hex(stimulus, INPUTS) == "1 f 1\n0 3 0\n"
    got = tb.hex_trace("a\nf\n\n", OUTPUTS)
    assert got == {"y": ["1010", "1111"]}
    assert tb.hex_trace("a b\n", OUTPUTS) == {"y": []}
