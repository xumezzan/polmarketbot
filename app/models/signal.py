from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import SignalStatus

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.execution_intent import ExecutionIntent
    from app.models.live_order import LiveOrder
    from app.models.live_position import LivePosition
    from app.models.position import Position
    from app.models.trade import PaperTrade


class Signal(TimestampMixin, Base):
    """Trading signal produced from an analysis and matched market."""

    __tablename__ = "signals"
    __table_args__ = (
        sa.CheckConstraint(
            "market_price >= 0 AND market_price <= 1",
            name="market_price_range",
        ),
        sa.CheckConstraint(
            "fair_probability >= 0 AND fair_probability <= 1",
            name="fair_probability_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        sa.ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    market_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    market_slug: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    market_question: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    market_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    execution_price: Mapped[float | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    raw_fair_probability: Mapped[float | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    fair_probability: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    raw_edge: Mapped[float | None] = mapped_column(sa.Numeric(6, 4), nullable=True)
    edge: Mapped[float] = mapped_column(sa.Numeric(6, 4), nullable=False)
    estimated_fee_rate: Mapped[float | None] = mapped_column(sa.Numeric(8, 6), nullable=True)
    estimated_fee_per_share: Mapped[float | None] = mapped_column(sa.Numeric(8, 6), nullable=True)
    market_consensus_weight: Mapped[float | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    calibration_sample_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    signal_status: Mapped[SignalStatus] = mapped_column(
        sa.Enum(SignalStatus, name="signal_status_enum"),
        nullable=False,
        index=True,
    )
    explanation: Mapped[str] = mapped_column(sa.Text, nullable=False)

    analysis: Mapped["Analysis"] = relationship(back_populates="signals")
    execution_intents: Mapped[list["ExecutionIntent"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
    )
    live_orders: Mapped[list["LiveOrder"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
    )
    live_positions: Mapped[list["LivePosition"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
    )
    paper_trades: Mapped[list["PaperTrade"]] = relationship(back_populates="signal")
    positions: Mapped[list["Position"]] = relationship(back_populates="signal")
