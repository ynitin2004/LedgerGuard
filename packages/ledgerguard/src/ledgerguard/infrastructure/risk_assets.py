"""Load versioned risk assets from the repository (``rules/``, ``policy/``, ``models/``).

File name = version: ``rules/v1.0.yaml`` holds rule set ``1.0``. The loader checks that
the version inside the file matches its name, so a copied file cannot silently claim
another version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ledgerguard.risk.model import LogisticModel
from ledgerguard.risk.policy import Policy
from ledgerguard.risk.rules import RuleSet


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _check_version(path: Path, declared: str) -> None:
    if path.stem != f"v{declared}":
        raise ValueError(f"{path}: declares version {declared!r} but is named {path.name!r}")


def load_rules(assets_dir: Path, version: str) -> RuleSet:
    path = assets_dir / "rules" / f"v{version}.yaml"
    rules = RuleSet.model_validate(_read_yaml(path))
    _check_version(path, rules.version)
    return rules


def load_policy(assets_dir: Path, version: str) -> Policy:
    path = assets_dir / "policy" / f"v{version}.yaml"
    policy = Policy.model_validate(_read_yaml(path))
    _check_version(path, policy.version)
    return policy


def load_model_file(path: Path) -> LogisticModel:
    return LogisticModel.model_validate(json.loads(path.read_text(encoding="utf-8")))
