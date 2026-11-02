"""Move independent probes' output path without changing a byte of attacks/assertions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE_NAME = "test_pr03a_second_review_regressions"
EXPECTED = "e5bd7b5849e9ac162e96e3cef994ed4e690b0b5ccb8db4aa193305b816e38c9e"


def pytest_collection_modifyitems(items):
    for item in items:
        if item.module.__name__ != PROBE_NAME:
            continue
        source = Path(item.module.__file__)
        original = source.read_bytes().split(b"\n# Third-round evidence adapter;", 1)[0]
        assert hashlib.sha256(original).hexdigest() == EXPECTED, "Independent probe attack/assertions changed"
        output = Path(os.environ["PR03A_THIRD_ROUND_EVIDENCE"]).resolve()
        assert (ROOT / "outputs/pr03a/third-round").resolve() in output.parents
        output.mkdir(parents=True, exist_ok=True)
        item.module.OUT = output
