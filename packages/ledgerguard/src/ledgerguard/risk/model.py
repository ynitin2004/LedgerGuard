"""Logistic-regression scoring from a JSON artifact (no numpy, no pickle).

Training (scripts/train_model.py, scikit-learn) exports standardisation parameters
and coefficients. Scoring here is a dot product and a sigmoid, so the API needs no
numerical libraries and never deserialises executable objects. A CI test checks this
implementation against scikit-learn's ``predict_proba``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ledgerguard.risk.features import FEATURE_NAMES
from ledgerguard.risk.policy import SCORE_DECIMALS


class LogisticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^v\d+$")
    algo: Literal["logistic_regression"]
    features: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    # Provenance and held-out metrics, written by the training script.
    metrics: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> LogisticModel:
        n = len(self.features)
        if not n or not (len(self.means) == len(self.scales) == len(self.coefficients) == n):
            raise ValueError("features, means, scales and coefficients must align")
        unknown = set(self.features) - set(FEATURE_NAMES)
        if unknown:
            raise ValueError(f"unknown features: {sorted(unknown)}")
        if any(s <= 0 for s in self.scales):
            raise ValueError("scales must be positive")
        return self

    def probability(self, features: Mapping[str, float]) -> float:
        z = self.intercept + math.fsum(
            c * (features[f] - m) / s
            for f, m, s, c in zip(
                self.features, self.means, self.scales, self.coefficients, strict=True
            )
        )
        # Numerically stable sigmoid.
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def score(self, features: Mapping[str, float]) -> float:
        return round(self.probability(features), SCORE_DECIMALS)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
