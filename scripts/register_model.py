"""Register a model artifact in model_registry (and optionally make it the active one).

    uv run python scripts/register_model.py models/lr-v1.json --activate

Connects as the schema owner. Versions are immutable: registering an existing version
with different contents fails; re-registering identical contents is a no-op (and can
still activate it). A running API picks up the first active model without a restart;
switching to a different model needs a restart.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from ledgerguard.config import get_settings
from ledgerguard.infrastructure.db.engine import create_session_factory
from ledgerguard.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from ledgerguard.infrastructure.risk_assets import load_model_file
from ledgerguard.risk.model import LogisticModel

ROOT = Path(__file__).resolve().parents[1]


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


async def register(
    model: LogisticModel, artifact_path: str, activate: bool, database_url: str
) -> str:
    trained_raw = model.training.get("trained_at")
    trained_at = datetime.fromisoformat(trained_raw) if trained_raw else datetime.now(UTC)

    engine = create_async_engine(database_url)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(engine))
        async with factory() as uow:
            existing = await uow.models.get(model.version)
            if existing is not None and existing.sha256() != model.sha256():
                raise SystemExit(
                    f"model {model.version} is already registered with different contents; "
                    "versions are immutable, train a new version instead"
                )
            if existing is None:
                await uow.models.register(
                    model, artifact_path=artifact_path, trained_at=trained_at, activate=activate
                )
                status = "registered"
            elif activate:
                await uow.models.activate(model.version)
                status = "already registered"
            else:
                status = "already registered"
            await uow.commit()
    finally:
        await engine.dispose()
    return f"{model.version} {status}{' and active' if activate else ''} (sha256 {model.sha256()})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a model artifact.")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    model = load_model_file(args.artifact)
    message = asyncio.run(
        register(
            model, _repo_relative(args.artifact), args.activate, get_settings().owner_database_url
        )
    )
    sys.stdout.write(message + "\n")


if __name__ == "__main__":
    main()
