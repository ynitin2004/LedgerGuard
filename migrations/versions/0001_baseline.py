"""Baseline: empty schema.

Establishes the migration history (and the ``alembic_version`` table that
/readyz checks) without creating speculative tables. The first real tables
(api_clients, transactions, idempotency_records, audit_events) arrive in M1.

No extensions are enabled: gen_random_uuid() is built into PostgreSQL 13+, and
pgvector is gated behind the M7 retrieval baseline (docs/DECISIONS.md, ADR-006).

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
