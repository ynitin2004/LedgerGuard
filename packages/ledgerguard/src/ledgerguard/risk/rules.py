"""Versioned rule sets (``rules/v<N>.yaml``).

A rule is data, not code: a list of ``feature <op> value`` conditions that must all
hold. No expression evaluation (no ``eval``), so a rules file cannot execute anything.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ledgerguard.risk.features import FEATURE_NAMES

Op = Literal[">", ">=", "<", "<=", "==", "!="]
_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature: str
    op: Op
    value: float

    def holds(self, features: Mapping[str, float]) -> bool:
        return _OPS[self.op](features[self.feature], self.value)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    description: str = Field(min_length=1)
    severity: Literal["hard", "soft"]
    # Soft rules add their weight to the combined score; hard rules decline outright.
    weight: float = Field(ge=0.0, le=1.0)
    when: tuple[Condition, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: str
    severity: str
    weight: float
    # The feature values the rule looked at, for explanations and evidence.
    inputs: dict[str, float]


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^\d+\.\d+$")
    rules: tuple[Rule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> RuleSet:
        codes = [r.code for r in self.rules]
        if len(codes) != len(set(codes)):
            raise ValueError("rule codes must be unique")
        for rule in self.rules:
            for cond in rule.when:
                if cond.feature not in FEATURE_NAMES:
                    raise ValueError(f"{rule.code}: unknown feature {cond.feature!r}")
            if rule.severity == "hard" and rule.weight != 0:
                raise ValueError(f"{rule.code}: hard rules decline outright; weight must be 0")
        return self

    def evaluate(self, features: Mapping[str, float]) -> list[RuleHit]:
        hits = []
        for rule in self.rules:
            if all(cond.holds(features) for cond in rule.when):
                inputs = {c.feature: features[c.feature] for c in rule.when}
                hits.append(RuleHit(rule.code, rule.severity, rule.weight, inputs))
        return hits
