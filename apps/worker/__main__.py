"""Worker entrypoint: ``uv run python -m apps.worker <role> [options]``.

Roles: ``janitor`` (M1). Planned: outbox-publisher, feature-updater (M3),
investigator (M4), webhook-sender (M5), rag-ingest (M7).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from apps.worker.janitor import run_janitor
from ledgerguard.config import get_settings
from ledgerguard.observability.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m apps.worker")
    roles = parser.add_subparsers(dest="role", required=True)

    janitor = roles.add_parser("janitor", help="purge expired idempotency records")
    janitor.add_argument("--once", action="store_true", help="run one pass and exit")
    janitor.add_argument("--interval", type=float, default=300.0, help="seconds between passes")
    janitor.add_argument("--batch-size", type=int, default=1000)

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if args.role == "janitor":
        asyncio.run(
            run_janitor(
                settings, once=args.once, interval_s=args.interval, batch_size=args.batch_size
            )
        )


if __name__ == "__main__":
    main()
