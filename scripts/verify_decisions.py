"""Replay every stored risk decision and check it reproduces exactly (plan s.7).

    uv run python scripts/verify_decisions.py            # exit 1 on any mismatch

Rules and thresholds are loaded by the versions stored on each decision, the model
from model_registry by its stored version. Read-only; runs as the app role.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.application.replay import replay
from ledgerguard.config import get_settings
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.infrastructure.risk_assets import load_policy, load_rules
from ledgerguard.risk.engine import RiskEngine


async def verify(limit: int | None) -> tuple[Counter[str], list[str]]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    engines: dict[tuple[str, str, str], RiskEngine] = {}
    tally: Counter[str] = Counter()
    failures: list[str] = []
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        async with factory() as uow:
            for txn_id in await uow.risk_decisions.transaction_ids(limit):
                for stored in await uow.risk_decisions.list_for_transaction(txn_id):
                    key = (stored.rules_version, stored.model_version, stored.thresholds_version)
                    if key not in engines:
                        model = await uow.models.get(stored.model_version)
                        if model is None:
                            raise SystemExit(f"model {stored.model_version} not in registry")
                        engines[key] = RiskEngine(
                            rules=load_rules(settings.risk_assets_dir, key[0]),
                            model=model,
                            policy=load_policy(settings.risk_assets_dir, key[2]),
                        )
                    result = replay(stored, engines[key])
                    tally["match" if result.matches else "MISMATCH"] += 1
                    if not result.matches:
                        failures.append(f"{result.decision_id}: {result.differences}")
    finally:
        await engine.dispose()
    return tally, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay stored risk decisions.")
    parser.add_argument("--limit", type=int, default=None, help="max transactions to check")
    args = parser.parse_args()
    tally, failures = asyncio.run(verify(args.limit))
    for line in failures[:20]:
        sys.stdout.write(line + "\n")
    sys.stdout.write(f"replayed {sum(tally.values())} decisions: {dict(tally)}\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
