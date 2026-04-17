from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ForecastObservation(TimestampMixin, Base):
    """Resolved forecast outcome used for calibration and Brier tracking."""

    __tablename__ = "forecast_observations"
    __table_args__ = (
        sa.CheckConstraint(
            "raw_probability >= 0 AND raw_probability <= 1",
            name="raw_probability_range",
        ),
        sa.CheckConstraint(
            "calibrated_probability >= 0 AND calibrated_probability <= 1",
            name="calibrated_probability_range",
        ),
        sa.CheckConstraint(
            "market_price >= 0 AND market_price <= 1",
            name="market_price_range",
        ),
        sa.CheckConstraint(
            "execution_price >= 0 AND execution_price <= 1",
            name="execution_price_range",
        ),
        sa.CheckConstraint(
            "outcome_value >= 0 AND outcome_value <= 1",
            name="outcome_value_range",
        ),
        sa.CheckConstraint(
            "brier_score >= 0 AND brier_score <= 1",
            name="brier_score_range",
        ),
        sa.UniqueConstraint("signal_id", name="uq_forecast_observations_signal_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        sa.ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_id: Mapped[int] = mapped_column(
        sa.ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("positions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    market_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(sa.String(50), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(sa.String(100), nullable=True, index=True)
    side: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True)
    raw_probability: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    calibrated_probability: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    market_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    execution_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    outcome_value: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    outcome_label: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    brier_score: Mapped[float] = mapped_column(sa.Numeric(8, 6), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
