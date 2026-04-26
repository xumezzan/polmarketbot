from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ReconciliationStatus


class ReconciliationRun(TimestampMixin, Base):
    """Phase-4 reconciliation audit snapshot."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    status: Mapped[ReconciliationStatus] = mapped_column(
        sa.Enum(ReconciliationStatus, name="reconciliation_status_enum"),
        nullable=False,
        index=True,
    )
    mismatch_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
