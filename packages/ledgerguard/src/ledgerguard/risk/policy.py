"""Decision policy (``policy/v<N>.yaml``): thresholds that turn scores and hits into a decision.

decline  if any hard rule hit, or combined >= decline_threshold
review   if any soft rule hit, or combined >= review_threshold
approve  otherwise
combined = clamp(model_score + sum(soft rule weights), 0, 1)
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ledgerguard.domain.transactions import Decision
from ledgerguard.risk.rules import RuleHit

SCORE_DECIMALS = 5  # matches numeric(6,5) in the database


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^\d+\.\d+$")
    review_threshold: float = Field(gt=0.0, le=1.0)
    decline_threshold: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Policy:
        if self.review_threshold >= self.decline_threshold:
            raise ValueError("review_threshold must be below decline_threshold")
        return self

    def combined_score(self, model_score: float, hits: Sequence[RuleHit]) -> float:
        soft = sum(h.weight for h in hits if h.severity == "soft")
        return round(min(1.0, max(0.0, model_score + soft)), SCORE_DECIMALS)

    def decide(self, combined: float, hits: Sequence[RuleHit]) -> Decision:
        if any(h.severity == "hard" for h in hits) or combined >= self.decline_threshold:
            return Decision.DECLINE
        if any(h.severity == "soft" for h in hits) or combined >= self.review_threshold:
            return Decision.REVIEW
        return Decision.APPROVE
