# rules/

Versioned rule sets, one file per version: `v1.0.yaml` is rule set `1.0` (the loader checks that the name matches). Each rule is data: `code`, `description`, `severity` (`hard` = decline, `soft` = review and add `weight` to the combined score) and `when`, a list of `feature <op> value` conditions that must all hold. There is no expression language and no `eval`.

**Released files are immutable.** A decision stores the rules version that produced it, and `tests/unit/test_risk_engine.py` pins each released file's SHA-256. To change a rule, add `v1.1.yaml` and point `LG_RULES_VERSION` at it. Feature names: `packages/ledgerguard/src/ledgerguard/risk/features.py`. Rationale: docs/DECISIONS.md ADR-020.
