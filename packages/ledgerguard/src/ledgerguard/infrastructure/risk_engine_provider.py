"""Supplies the active risk engine: rules + policy from files, model from the registry.

Loaded lazily and cached. If loading fails (e.g. no model registered yet) the error is
kept and retried on the next call, so registering a model makes a running API ready
without a restart. Activating a *different* model needs a restart (by design: one
process serves one policy_version at a time).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ledgerguard.application.ports import RiskEngineUnavailableError, UnitOfWorkFactory
from ledgerguard.infrastructure.risk_assets import load_policy, load_rules
from ledgerguard.risk.engine import RiskEngine


class RiskEngineProvider:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        assets_dir: Path,
        rules_version: str,
        policy_version: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._assets_dir = assets_dir
        self._rules_version = rules_version
        self._policy_version = policy_version
        self._engine: RiskEngine | None = None
        self._lock = asyncio.Lock()

    async def current(self) -> RiskEngine:
        if self._engine is not None:
            return self._engine
        async with self._lock:
            if self._engine is None:
                self._engine = await self._load()
            return self._engine

    async def _load(self) -> RiskEngine:
        try:
            rules = load_rules(self._assets_dir, self._rules_version)
            policy = load_policy(self._assets_dir, self._policy_version)
            async with self._uow_factory() as uow:
                model = await uow.models.get_active()
        except Exception as exc:
            raise RiskEngineUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if model is None:
            raise RiskEngineUnavailableError("no active model in model_registry")
        return RiskEngine(rules=rules, model=model, policy=policy)
