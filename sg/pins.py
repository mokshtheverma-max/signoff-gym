"""Pinned image and thread count, read from pins.json (PLAN.md P0.1)."""

import json
import os

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))


def load(path=None):
    """Return the pins as a dict. Raises if a required key is missing."""
    path = path or os.path.join(REPO, "pins.json")
    with open(path, encoding="utf-8") as handle:
        pins = json.load(handle)
    for key in ("orfs_image", "orfs_digest", "platform", "num_cores"):
        if key not in pins:
            raise KeyError(f"pins.json lacks {key}")
    if not pins["orfs_image"].endswith("@" + pins["orfs_digest"]):
        raise ValueError("orfs_image must reference orfs_digest")
    if not isinstance(pins["num_cores"], int) or pins["num_cores"] < 1:
        raise ValueError("num_cores must be a positive integer")
    return pins
