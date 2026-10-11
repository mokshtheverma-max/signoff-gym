"""The hidden testbench's pure parts (PLAN.md F2.5 D).

Part of Signoff Gym by Moksh Verma.

A stimulus is a list of cycles, each (rst, {input role: value}). It opens
with RESET_CYCLES of reset, then random cycles from the first seed, then
each directed corner case held for its hold count, a reset in the middle,
and random cycles from the second seed. The reference model gives the
expected outputs of the unmodified design, cycle by cycle.

The design under test is compared with its outputs shifted by the declared
added latency d: at every cycle t whose W cycles before it and d cycles
from it on have reset low, the design's outputs at t + d must equal the
model's at t, bit for bit. An x or z bit never matches.

Simulator input and output: a Yosys witness trace (the format of Yosys's
own ywio.py) for Yosys `sim -r`, and the VCD file `sim -vcd` writes, read
here at one sample per step. A two-state simulator's hex trace is read too.
Everything here is pure.
"""

import json
import random

RESET_CYCLES = 2
STEP = 10  # time units per clock cycle in Yosys sim's VCD (sim -width 10)


def to_bits(value, width):
    """A value as width bits, most significant first (two's complement)."""
    return format(value & ((1 << width) - 1), f"0{width}b")


def random_vector(inputs, rng):
    """One cycle of random inputs, uniform over each input's range."""
    out = {}
    for role, width, signed in inputs:
        raw = rng.getrandbits(width)
        out[role] = raw - (1 << width) if signed and raw >> (width - 1) else raw
    return out


def build(inputs, directed, hold, seeds, random_cycles, tail_cycles):
    """The whole stimulus as a list of (rst, {role: value})."""
    zeros = {role: 0 for role, _, _ in inputs}
    first, second = random.Random(seeds[0]), random.Random(seeds[1])
    cycles = [(1, dict(zeros))] * RESET_CYCLES
    cycles += [(0, random_vector(inputs, first)) for _ in range(random_cycles)]
    for vector in directed:
        full = dict(zeros, **{k: v for k, v in vector.items() if k in zeros})
        cycles += [(0, full)] * hold
    cycles += [(1, dict(zeros))] * RESET_CYCLES
    cycles += [(0, random_vector(inputs, second)) for _ in range(tail_cycles)]
    return cycles


def compared_cycles(resets, warmup, added):
    """The cycles t the comparison checks: reset low from t - W to t + d - 1."""
    prefix = [0]
    for value in resets:
        prefix.append(prefix[-1] + (1 if value else 0))
    return [t for t in range(warmup, len(resets) - added)
            if prefix[t + added] - prefix[t - warmup] == 0]


def compare(stimulus, expected, got, outputs, warmup, added):
    """(ok, first mismatch or None, cycles compared). got maps each output
    role to one bit string per cycle of the design's run."""
    n = len(stimulus)
    for role, _, _ in outputs:
        if len(got.get(role, ())) < n:
            return False, {"problem": f"the run stopped early: {len(got.get(role, ()))} of {n} cycles of {role}"}, 0
    resets = [rst for rst, _ in stimulus]
    cycles = compared_cycles(resets, warmup, added)
    for done, t in enumerate(cycles):
        for role, width, _ in outputs:
            want = to_bits(expected[t][role], width)
            have = got[role][t + added]
            if have != want:
                return False, {"cycle": t, "port": role, "expected": want, "got": have}, done
    return bool(cycles), None if cycles else {"problem": "no cycle to compare"}, len(cycles)


def witness(stimulus, inputs, names, reset_name):
    """A Yosys witness trace of the stimulus (JSON text). inputs is the
    model's [(role, width, signed)]; names maps roles to port names."""
    signals = sorted([(names[role], width) for role, width, _ in inputs] + [(reset_name, 1)])
    roles = {names[role]: role for role, _, _ in inputs}
    steps = []
    for rst, values in stimulus:
        bits = []
        for name, width in signals:
            value = rst if name == reset_name else values[roles[name]]
            bits.append(to_bits(value, width))
        # Bit 0 of the first signal is the last character (ywio.py's packing).
        steps.append({"bits": "".join(bits[::-1])})
    doc = {"format": "Yosys Witness Trace", "generator": "signoffgym",
           "clocks": [{"path": ["\\clk"], "edge": "posedge", "offset": 0}],
           "signals": [{"path": [f"\\{name}"], "width": width, "offset": 0, "init_only": False}
                       for name, width in signals],
           "steps": steps}
    return json.dumps(doc, sort_keys=True)


def read_vcd(text, wanted, steps):
    """{port name: [bit string per step]} from Yosys sim's VCD, sampling
    each port at time STEP * t after every change at that time."""
    ids, widths = {}, {}
    lines = text.split("\n")
    body = 0
    for index, line in enumerate(lines):
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "$var" and parts[4] in wanted:
            ids[parts[3]] = parts[4]
            widths[parts[4]] = int(parts[2])
        if line.startswith("$enddefinitions"):
            body = index + 1
            break
    current = {name: "x" * widths[name] for name in widths}
    samples = {name: [] for name in widths}
    step = 0
    time = None

    def sample_until(limit):
        nonlocal step
        while step < steps and step * STEP < limit:
            for name in samples:
                samples[name].append(current[name])
            step += 1

    for line in lines[body:]:
        if not line:
            continue
        head = line[0]
        if head == "#":
            new = int(line[1:])
            if time is not None:
                sample_until(new)
            time = new
        elif head in "bB":
            value, ident = line[1:].split()
            name = ids.get(ident)
            if name:
                width = widths[name]
                value = value.lower()
                pad = value[0] if value[0] in "xz" else "0"
                current[name] = (pad * (width - len(value)) + value)[-width:]
        elif head in "01xzXZ":
            name = ids.get(line[1:].strip())
            if name:
                current[name] = head.lower()
    sample_until(float("inf"))
    missing = [name for name in wanted if name not in widths]
    return samples, missing


def hex_trace(text, outputs):
    """{role: [bit string per cycle]} from a two-state simulator's trace:
    one line per cycle, one hex value per output in the order given."""
    got = {role: [] for role, _, _ in outputs}
    for line in text.split("\n"):
        if not line.strip():
            continue
        values = line.split()
        if len(values) != len(outputs):
            break
        for (role, width, _), value in zip(outputs, values):
            got[role].append(to_bits(int(value, 16), width))
    return got


def stimulus_hex(stimulus, inputs):
    """The stimulus for a two-state simulator's driver: rst then each input
    in model order, hex, one line per cycle."""
    lines = []
    for rst, values in stimulus:
        fields = [format(rst, "x")] + [format(values[role] & ((1 << width) - 1), "x")
                                       for role, width, _ in inputs]
        lines.append(" ".join(fields))
    return "\n".join(lines) + "\n"
