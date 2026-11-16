"""Decision replay: recompute a stored decision from its feature snapshot and versions.

Plan s.7 acceptance: replaying a stored ``feature_snapshot`` through the same rules,
model and thresholds versions must reproduce the decision exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ledgerguard.application.ports import StoredRiskDecision
from ledgerguard.risk.engine import RiskEngine


@dataclass(frozen=True, slots=True)
class ReplayResult:
    decision_id: str
    matches: bool
    differences: dict[str, tuple[object, object]]  # field -> (stored, recomputed)


def replay(stored: StoredRiskDecision, engine: RiskEngine) -> ReplayResult:
    if (engine.rules.version, engine.model.version, engine.policy.version) != (
        stored.rules_version,
        stored.model_version,
        stored.thresholds_version,
    ):
        raise ValueError("engine versions differ from the stored decision's versions")
    outcome = engine.evaluate(stored.feature_snapshot)
    pairs: dict[str, tuple[object, object]] = {
        "decision": (stored.decision, outcome.decision.value),
        "score": (stored.score, Decimal(str(outcome.score)).quantize(Decimal("0.00001"))),
        "model_score": (
            stored.model_score,
            Decimal(str(outcome.model_score)).quantize(Decimal("0.00001")),
        ),
        "reason_codes": (stored.reason_codes, outcome.reason_codes),
        "policy_version": (stored.policy_version, outcome.policy_version),
    }
    differences = {k: v for k, v in pairs.items() if v[0] != v[1]}
    return ReplayResult(str(stored.id), not differences, differences)
