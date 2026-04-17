from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import MarketSide, TradeStatus

if TYPE_CHECKING:
    from app.models.position import Position
    from app.models.signal import Signal


class PaperTrade(TimestampMixin, Base):
    """Virtual trade used to test the strategy before real money."""

    __tablename__ = "paper_trades"
    __table_args__ = (
        sa.CheckConstraint("entry_price >= 0 AND entry_price <= 1", name="entry_price_range"),
        sa.CheckConstraint(
            "exit_price IS NULL OR (exit_price >= 0 AND exit_price <= 1)",
            name="exit_price_range",
        ),
        sa.CheckConstraint("size_usd >= 0", name="size_usd_non_negative"),
        sa.CheckConstraint("shares >= 0", name="shares_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        sa.ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("positions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    market_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    side: Mapped[MarketSide] = mapped_column(
        sa.Enum(MarketSide, name="market_side_enum"),
        nullable=False,
    )
    entry_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    size_usd: Mapped[float] = mapped_column(sa.Numeric(12, 2), nullable=False)
    shares: Mapped[float] = mapped_column(sa.Numeric(18, 6), nullable=False)
    pnl: Mapped[float | None] = mapped_column(sa.Numeric(12, 4), nullable=True)
    status: Mapped[TradeStatus] = mapped_column(
        sa.Enum(TradeStatus, name="trade_status_enum"),
        nullable=False,
        index=True,
    )
    close_reason: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    resolution_outcome: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="paper_trades")
    position: Mapped["Position | None"] = relationship(back_populates="paper_trades")
