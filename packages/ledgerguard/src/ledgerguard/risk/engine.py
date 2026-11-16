"""The risk engine: one versioned (rules, model, policy) triple applied to a feature snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ledgerguard.domain.transactions import Decision
from ledgerguard.risk.model import LogisticModel
from ledgerguard.risk.policy import Policy
from ledgerguard.risk.rules import RuleHit, RuleSet


def composite_version(rules_version: str, model_version: str, thresholds_version: str) -> str:
    """Plan A10: ``rules@x.y+model@vN+thresholds@z``. Changing any part changes it."""
    return f"rules@{rules_version}+model@{model_version}+thresholds@{thresholds_version}"


@dataclass(frozen=True, slots=True)
class RiskOutcome:
    decision: Decision
    score: float  # combined score, what the policy thresholds compare against
    model_score: float
    hits: tuple[RuleHit, ...]
    rules_version: str
    model_version: str
    thresholds_version: str

    @property
    def policy_version(self) -> str:
        return composite_version(self.rules_version, self.model_version, self.thresholds_version)

    @property
    def reason_codes(self) -> list[str]:
        return [h.code for h in self.hits]


@dataclass(frozen=True, slots=True)
class RiskEngine:
    rules: RuleSet
    model: LogisticModel
    policy: Policy

    @property
    def policy_version(self) -> str:
        return composite_version(self.rules.version, self.model.version, self.policy.version)

    def evaluate(self, features: Mapping[str, float]) -> RiskOutcome:
        hits = tuple(self.rules.evaluate(features))
        model_score = self.model.score(features)
        combined = self.policy.combined_score(model_score, hits)
        return RiskOutcome(
            decision=self.policy.decide(combined, hits),
            score=combined,
            model_score=model_score,
            hits=hits,
            rules_version=self.rules.version,
            model_version=self.model.version,
            thresholds_version=self.policy.version,
        )
