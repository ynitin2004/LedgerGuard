"""Rules, policy, model scoring and the engine: versioning, determinism, replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from ledgerguard.domain.transactions import Decision
from ledgerguard.infrastructure.risk_assets import load_model_file, load_policy, load_rules
from ledgerguard.risk.engine import RiskEngine, composite_version
from ledgerguard.risk.features import FEATURE_NAMES
from ledgerguard.risk.model import LogisticModel
from ledgerguard.risk.policy import Policy
from ledgerguard.risk.rules import RuleHit, RuleSet

ROOT = Path(__file__).resolve().parents[2]

# Released assets are immutable: change the file => bump its version (new file).
RELEASED = {
    "rules/v1.0.yaml": "f7c703e02e47babe3f06775579e71eae9e43c4a8844b5180eb763ca4c217eb92",
    "policy/v1.0.yaml": "bab22e0208faca0a17b214a3833fd1153698bcfaa3f23ab9e1cc00b0b83370c3",
}

BASE: dict[str, float] = {
    "amount_minor": 2500,
    "log_amount": 7.824446,
    "velocity_5m": 0,
    "velocity_1h": 0,
    "history_count_30d": 20,
    "amount_zscore": 0.1,
    "is_new_merchant": 0,
    "category_share_30d": 0.4,
    "failed_24h": 0,
}


@pytest.fixture(scope="module")
def engine() -> RiskEngine:
    return RiskEngine(
        rules=load_rules(ROOT, "1.0"),
        model=load_model_file(ROOT / "models" / "lr-v1.json"),
        policy=load_policy(ROOT, "1.0"),
    )


@pytest.mark.parametrize(("path", "digest"), sorted(RELEASED.items()))
def test_released_assets_are_unchanged(path: str, digest: str) -> None:
    content = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(content).hexdigest() == digest, (
        f"{path} changed: released versions are immutable, add a new version file instead"
    )


def test_engine_reports_composite_version(engine: RiskEngine) -> None:
    assert engine.policy_version == "rules@1.0+model@v1+thresholds@1.0"
    assert composite_version("1.0", "v2", "1.0") != engine.policy_version


def test_ordinary_payment_is_approved(engine: RiskEngine) -> None:
    outcome = engine.evaluate(BASE)
    assert outcome.decision is Decision.APPROVE
    assert outcome.reason_codes == []
    assert 0 <= outcome.model_score < 0.3


@pytest.mark.parametrize(
    ("overrides", "decision", "codes"),
    [
        ({"velocity_5m": 4, "velocity_1h": 4}, Decision.REVIEW, ["VELOCITY_5M"]),
        ({"failed_24h": 3}, Decision.DECLINE, ["REPEAT_FAIL_24H"]),
        ({"amount_minor": 1_000_000}, Decision.DECLINE, ["HIGH_AMOUNT_ABS"]),
        ({"amount_zscore": 3.6}, Decision.REVIEW, ["AMOUNT_ZSCORE"]),
        # Rules with history guards stay silent for cold-start customers.
        ({"amount_zscore": 3.6, "history_count_30d": 4}, None, []),
    ],
)
def test_rules_drive_decisions(
    engine: RiskEngine,
    overrides: dict[str, float],
    decision: Decision | None,
    codes: list[str],
) -> None:
    outcome = engine.evaluate({**BASE, **overrides})
    assert outcome.reason_codes == codes
    if decision is not None:
        assert outcome.decision is decision


def test_policy_thresholds() -> None:
    policy = Policy(version="9.9", review_threshold=0.3, decline_threshold=0.8)
    soft = RuleHit("S", "soft", 0.35, {})
    hard = RuleHit("H", "hard", 0.0, {})
    assert policy.decide(0.1, []) is Decision.APPROVE
    assert policy.decide(0.3, []) is Decision.REVIEW
    assert policy.decide(0.8, []) is Decision.DECLINE
    assert policy.decide(0.0, [soft]) is Decision.REVIEW
    assert policy.decide(0.0, [hard]) is Decision.DECLINE
    assert policy.combined_score(0.9, [soft, soft]) == 1.0  # clamped
    with pytest.raises(ValidationError):
        Policy(version="9.9", review_threshold=0.8, decline_threshold=0.3)


def _ruleset(**rule: Any) -> dict[str, Any]:
    base = {
        "code": "X",
        "description": "d",
        "severity": "soft",
        "weight": 0.1,
        "when": [{"feature": "velocity_5m", "op": ">=", "value": 1}],
    }
    return {"version": "1.0", "rules": [{**base, **rule}]}


@pytest.mark.parametrize(
    "bad",
    [
        _ruleset(when=[{"feature": "not_a_feature", "op": ">=", "value": 1}]),
        _ruleset(when=[{"feature": "velocity_5m", "op": "~", "value": 1}]),
        _ruleset(severity="hard", weight=0.5),
        _ruleset(code="lower_case"),
        _ruleset(when=[]),
        {"version": "1.0", "rules": [_ruleset()["rules"][0], _ruleset()["rules"][0]]},
        {**_ruleset(), "extra": True},
    ],
)
def test_invalid_rule_sets_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RuleSet.model_validate(bad)


def test_asset_file_name_must_match_declared_version(tmp_path: Path) -> None:
    (tmp_path / "rules").mkdir()
    text = (ROOT / "rules" / "v1.0.yaml").read_text(encoding="utf-8")
    (tmp_path / "rules" / "v2.0.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="declares version"):
        load_rules(tmp_path, "2.0")


def test_model_artifact_is_consistent_and_hash_is_stable() -> None:
    model = load_model_file(ROOT / "models" / "lr-v1.json")
    assert set(model.features) <= set(FEATURE_NAMES)
    assert model.sha256() == load_model_file(ROOT / "models" / "lr-v1.json").sha256()
    data = json.loads(model.canonical_json())
    data["coefficients"][0] += 1e-9
    assert LogisticModel.model_validate(data).sha256() != model.sha256()


def test_sigmoid_is_stable_for_extreme_inputs() -> None:
    model = LogisticModel(
        version="v9",
        algo="logistic_regression",
        features=("velocity_5m",),
        means=(0.0,),
        scales=(1.0,),
        coefficients=(1.0,),
        intercept=0.0,
    )
    assert model.probability({"velocity_5m": 1000}) == 1.0
    assert model.probability({"velocity_5m": -1000}) == 0.0
    assert model.probability({"velocity_5m": 0}) == 0.5


snapshots = st.fixed_dictionaries(
    {
        "amount_minor": st.integers(1, 2_000_000),
        "log_amount": st.floats(0, 15, allow_nan=False).map(lambda v: round(v, 6)),
        "velocity_5m": st.integers(0, 20),
        "velocity_1h": st.integers(0, 40),
        "history_count_30d": st.integers(0, 300),
        "amount_zscore": st.floats(-10, 10, allow_nan=False).map(lambda v: round(v, 6)),
        "is_new_merchant": st.integers(0, 1),
        "category_share_30d": st.floats(0, 1, allow_nan=False).map(lambda v: round(v, 6)),
        "failed_24h": st.integers(0, 10),
    }
)


@given(snapshots)
def test_replay_from_stored_json_reproduces_the_decision(snapshot: dict[str, float]) -> None:
    # The snapshot goes through JSON (as jsonb does) and must give the same outcome.
    engine = RiskEngine(
        rules=load_rules(ROOT, "1.0"),
        model=load_model_file(ROOT / "models" / "lr-v1.json"),
        policy=load_policy(ROOT, "1.0"),
    )
    first = engine.evaluate(snapshot)
    again = engine.evaluate(json.loads(json.dumps(snapshot)))
    assert again == first
    assert 0.0 <= first.score <= 1.0
    assert first.score >= first.model_score or first.score == 1.0
