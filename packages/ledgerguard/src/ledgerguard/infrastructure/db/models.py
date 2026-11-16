"""ORM table definitions (SQLAlchemy 2.x, ``Mapped[]``).

Schema rules (docs/DATA_MODEL.md): UUID keys from ``gen_random_uuid()``,
``created_at timestamptz DEFAULT now()``, money as ``amount_minor BIGINT`` +
``currency CHAR(3)``, enumerations as text + CHECK (cheaper to evolve than PG enums).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ledgerguard.infrastructure.db.base import Base


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ApiClientRow(Base):
    __tablename__ = "api_clients"
    __table_args__ = (CheckConstraint("status IN ('active', 'revoked')", name="status"),)

    id: Mapped[UUID] = _uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created_at()


class TransactionRow(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_minor_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint(
            "status IN ('created', 'evaluated', 'settled_sim', 'failed_sim')", name="status"
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('approve', 'decline', 'review')", name="decision"
        ),
        Index("ix_transactions_client_id_created_at", "client_id", text("created_at DESC")),
        # Feature history lookup is always tenant-scoped (M2): client first.
        Index(
            "ix_transactions_client_id_customer_token_created_at",
            "client_id",
            "customer_token",
            text("created_at DESC"),
        ),
        Index("ix_transactions_merchant_id_created_at", "merchant_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = _uuid_pk()
    client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("api_clients.id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_category: Mapped[str] = mapped_column(CHAR(4), nullable=False)
    customer_token: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Risk outcome and the versions that produced it: NULL until M2 evaluates.
    decision: Mapped[str | None] = mapped_column(Text)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    policy_version: Mapped[str | None] = mapped_column(Text)
    rules_version: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created_at()


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        PrimaryKeyConstraint("client_id", "key"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("api_clients.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    # NULL only inside the claiming transaction; set before it commits.
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    """Append-only: the app role has INSERT/SELECT only (migration 0002)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_resource_type_resource_id", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    # "metadata" is reserved on declarative classes, hence the attribute name.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()


class RiskDecisionRow(Base):
    """One evaluation of a transaction. Never updated: overrides add a new row (plan A6).

    ``feature_snapshot`` + the three versions are enough to recompute ``decision``,
    ``score`` and ``model_score`` exactly (replay test).
    """

    __tablename__ = "risk_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('approve', 'decline', 'review')", name="decision"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("model_score >= 0 AND model_score <= 1", name="model_score_range"),
        Index("ix_risk_decisions_transaction_id", "transaction_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    transaction_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False
    )
    rules_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    thresholds_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    model_score: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class ModelRegistryRow(Base):
    """Registered model artifacts. Exactly one may be active (partial unique index)."""

    __tablename__ = "model_registry"
    __table_args__ = (
        Index(
            "uq_model_registry_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    algo: Mapped[str] = mapped_column(Text, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    # The full artifact lives in the database, so serving and replay never depend on
    # files on the API host; the hash ties it to the file in models/.
    artifact: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()
