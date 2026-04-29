from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AnomalyObservation(TimestampMixin, Base):
    """Read-only observation captured by Anomaly Hunter during bot operation."""

    __tablename__ = "anomaly_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[str | None] = mapped_column(sa.String(80), nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    observation_type: Mapped[str] = mapped_column(sa.String(80), nullable=False, index=True)
    subject_type: Mapped[str | None] = mapped_column(sa.String(50), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(sa.String(20), nullable=False, index=True)
    score: Mapped[float] = mapped_column(sa.Numeric(7, 2), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class AnomalyHypothesis(TimestampMixin, Base):
    """Aggregated anomaly hypothesis generated from recent observations."""

    __tablename__ = "anomaly_hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    hypothesis_type: Mapped[str] = mapped_column(sa.String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(sa.String(30), nullable=False, index=True)
    score: Mapped[float] = mapped_column(sa.Numeric(7, 2), nullable=False, index=True)
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    rationale: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
