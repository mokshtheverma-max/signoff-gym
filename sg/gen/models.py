"""Reference models for Signoff Gym's generated designs (PLAN.md F2.5 D and I).

Part of Signoff Gym by Moksh Verma.

Pure Python, standard library only. A byte copy goes into each generated
task's verifier (tests/sg/models.py) and into the agent's workspace
(spec/model.py), with the instance's parameters beside it (params.json).

Each model is cycle-accurate at the unmodified design's own latency, with a
synchronous, active-high reset. step(inputs, rst) returns the outputs the
design shows during this cycle (its output registers), then applies the
rising clock edge that ends the cycle, with these inputs. Values are Python
integers: signed ports hold two's-complement values in their range,
unsigned ports values from 0 to 2**width - 1. Inputs and outputs are named
by role; params.json maps each role to its port name.
"""


def wrap(value, width, signed):
    """value reduced to a width-bit register, as the hardware holds it."""
    value &= (1 << width) - 1
    if signed and value >> (width - 1):
        value -= 1 << width
    return value


def clamp(value, width):
    """value saturated to a width-bit signed range."""
    top = (1 << (width - 1)) - 1
    return max(-top - 1, min(top, value))


def batcher_network(lanes):
    """Batcher's odd-even merge sort for a power-of-two lane count, as
    layers of (low lane, high lane) compare-exchange pairs. After a pair the
    low lane holds the smaller value and the high lane the larger."""
    pairs = []

    def merge(lo, n, r):
        step = r * 2
        if step < n:
            merge(lo, n, step)
            merge(lo + r, n, step)
            for i in range(lo + r, lo + n - r, step):
                pairs.append((i, i + r))
        else:
            pairs.append((lo, lo + r))

    def sort(lo, n):
        if n > 1:
            m = n // 2
            sort(lo, m)
            sort(lo + m, m)
            merge(lo, n, 1)

    if lanes < 2 or lanes & (lanes - 1):
        raise ValueError("lanes must be a power of two, at least 2")
    sort(0, lanes)
    layers, ready = [], [0] * lanes
    for low, high in pairs:
        depth = max(ready[low], ready[high])
        while len(layers) <= depth:
            layers.append([])
        layers[depth].append((low, high))
        ready[low] = ready[high] = depth + 1
    return layers


class Fir:
    """y = sum of coef[k] * x delayed k + 1 cycles; x is registered on the
    way in and y on the way out (latency 2)."""

    latency = 2

    def __init__(self, params):
        self.coef = list(params["coef"])
        self.width = params["width"]
        self.out_width = params["out_width"]
        self.inputs = [("x", self.width, True)]
        self.outputs = [("y", self.out_width, True)]
        self.reset()

    def reset(self):
        self.line = [0] * len(self.coef)
        self.y = 0

    def step(self, inputs, rst):
        out = {"y": self.y}
        if rst:
            self.reset()
        else:
            self.y = wrap(sum(c * d for c, d in zip(self.coef, self.line)), self.out_width, True)
            self.line = [inputs["x"]] + self.line[:-1]
        return out


class Mac:
    """y = the sum of a[i] * b[i], saturated to out_width bits; inputs are
    registered, the sum is registered, then saturated into y (latency 3)."""

    latency = 3

    def __init__(self, params):
        self.pairs = params["pairs"]
        self.width = params["width"]
        self.out_width = params["out_width"]
        self.inputs = ([(f"a{i}", self.width, True) for i in range(self.pairs)]
                       + [(f"b{i}", self.width, True) for i in range(self.pairs)])
        self.outputs = [("y", self.out_width, True)]
        self.reset()

    def reset(self):
        self.a = [0] * self.pairs
        self.b = [0] * self.pairs
        self.q = 0
        self.y = 0

    def step(self, inputs, rst):
        out = {"y": self.y}
        if rst:
            self.reset()
        else:
            self.y = clamp(self.q, self.out_width)
            self.q = sum(x * y for x, y in zip(self.a, self.b))
            self.a = [inputs[f"a{i}"] for i in range(self.pairs)]
            self.b = [inputs[f"b{i}"] for i in range(self.pairs)]
        return out


class Sorter:
    """The lanes' values sorted in ascending order (y0 smallest); inputs
    and outputs registered (latency 2)."""

    latency = 2

    def __init__(self, params):
        self.lanes = params["lanes"]
        self.width = params["width"]
        self.inputs = [(f"x{i}", self.width, False) for i in range(self.lanes)]
        self.outputs = [(f"y{i}", self.width, False) for i in range(self.lanes)]
        self.reset()

    def reset(self):
        self.x = [0] * self.lanes
        self.y = [0] * self.lanes

    def step(self, inputs, rst):
        out = {f"y{i}": self.y[i] for i in range(self.lanes)}
        if rst:
            self.reset()
        else:
            self.y = sorted(self.x)
            self.x = [inputs[f"x{i}"] for i in range(self.lanes)]
        return out


ALU_OPS = ("add", "sub", "and", "xor", "shl", "sra", "mul", "slt")


class Alu:
    """y = op(a, b) for a 3-bit op code (ALU_OPS order): add, subtract,
    and, xor, shift left by b's low bits, arithmetic shift right by b's low
    bits, the low half of a * b, and 1 if a < b as signed numbers (else 0);
    inputs and op registered, y registered (latency 2)."""

    latency = 2

    def __init__(self, params):
        self.width = params["width"]
        self.shift_bits = (self.width - 1).bit_length()
        self.inputs = [("a", self.width, False), ("b", self.width, False), ("op", 3, False)]
        self.outputs = [("y", self.width, False)]
        self.reset()

    def reset(self):
        self.a = self.b = self.op = self.y = 0

    def compute(self, op, a, b):
        w = self.width
        mask = (1 << w) - 1
        amount = b & ((1 << self.shift_bits) - 1)
        sa, sb = wrap(a, w, True), wrap(b, w, True)
        name = ALU_OPS[op]
        if name == "add":
            return (a + b) & mask
        if name == "sub":
            return (a - b) & mask
        if name == "and":
            return a & b
        if name == "xor":
            return a ^ b
        if name == "shl":
            return (a << amount) & mask
        if name == "sra":
            return (sa >> amount) & mask
        if name == "mul":
            return (a * b) & mask
        return 1 if sa < sb else 0

    def step(self, inputs, rst):
        out = {"y": self.y}
        if rst:
            self.reset()
        else:
            self.y = self.compute(self.op, self.a, self.b)
            self.a, self.b, self.op = inputs["a"], inputs["b"], inputs["op"]
        return out


class Systolic:
    """A weight-stationary array of cells: y = the sum over i of w[i] times
    x[i], with the inputs as they were 2 cycles earlier; inputs and output
    registered (latency 2). The sum is exact in out_width bits."""

    latency = 2

    def __init__(self, params):
        self.weights = list(params["weights"])
        self.width = params["width"]
        self.out_width = params["out_width"]
        self.cells = len(self.weights)
        self.inputs = [(f"x{i}", self.width, True) for i in range(self.cells)]
        self.outputs = [("y", self.out_width, True)]
        self.reset()

    def reset(self):
        self.x = [0] * self.cells
        self.y = 0

    def step(self, inputs, rst):
        out = {"y": self.y}
        if rst:
            self.reset()
        else:
            self.y = wrap(sum(w * v for w, v in zip(self.weights, self.x)), self.out_width, True)
            self.x = [inputs[f"x{i}"] for i in range(self.cells)]
        return out


FAMILIES = {"fir": Fir, "mac": Mac, "sort": Sorter, "alu": Alu, "systolic": Systolic}


def make(family, params):
    """The reference model of one generated design."""
    return FAMILIES[family](params)


def run(model, stimulus):
    """Outputs per cycle for a stimulus: a list of (rst, {role: value})."""
    model.reset()
    return [model.step(inputs, rst) for rst, inputs in stimulus]
