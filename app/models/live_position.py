from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import LivePositionStatus, MarketSide

if TYPE_CHECKING:
    from app.models.live_order import LiveOrder
    from app.models.signal import Signal


class LivePosition(TimestampMixin, Base):
    """Open or closed live position created from a filled exchange order."""

    __tablename__ = "live_positions"
    __table_args__ = (
        sa.CheckConstraint("entry_price >= 0 AND entry_price <= 1", name="live_position_entry_price_range"),
        sa.CheckConstraint("size_usd >= 0", name="live_position_size_non_negative"),
        sa.CheckConstraint("shares >= 0", name="live_position_shares_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        sa.ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    live_order_id: Mapped[int] = mapped_column(
        sa.ForeignKey("live_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    market_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    market_question: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    side: Mapped[MarketSide] = mapped_column(
        sa.Enum(MarketSide, name="market_side_enum"),
        nullable=False,
    )
    token_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    entry_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    size_usd: Mapped[float] = mapped_column(sa.Numeric(12, 2), nullable=False)
    shares: Mapped[float] = mapped_column(sa.Numeric(18, 6), nullable=False)
    status: Mapped[LivePositionStatus] = mapped_column(
        sa.Enum(LivePositionStatus, name="live_position_status_enum"),
        nullable=False,
        index=True,
    )
    close_reason: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="live_positions")
    live_order: Mapped["LiveOrder"] = relationship(back_populates="live_positions")
