"""The committed model artifact is reproducible and the API's scoring matches scikit-learn.

Needs numpy/scikit-learn. They cannot load on the primary Windows dev PC (Application
Control, ADR-021), so this module is skipped there and runs in CI (Linux).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _ml_available() -> bool:
    try:
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _ml_available(), reason="numpy/scikit-learn cannot load here")


def _train_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "train_model", ROOT / "scripts" / "train_model.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["train_model"] = module
    spec.loader.exec_module(module)
    return module


def test_retraining_reproduces_the_committed_artifact() -> None:
    committed = json.loads((ROOT / "models" / "lr-v1.json").read_text(encoding="utf-8"))
    fresh = _train_module().train(
        seed=committed["training"]["seed"], rules_version="1.0", policy_version="1.0"
    )
    assert fresh["features"] == committed["features"]
    for key in ("means", "scales", "coefficients"):
        for a, b in zip(fresh[key], committed[key], strict=True):
            assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9), key
    assert math.isclose(fresh["intercept"], committed["intercept"], rel_tol=1e-6)
    # MODEL_CARD.md quotes these numbers: they must be what the script produces.
    assert fresh["metrics"]["model"] == committed["metrics"]["model"]
    assert fresh["metrics"]["policy"] == committed["metrics"]["policy"]
    assert fresh["metrics"]["parity_max_abs_diff"] < 1e-12
