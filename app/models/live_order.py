from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import LiveOrderStatus, MarketSide

if TYPE_CHECKING:
    from app.models.execution_intent import ExecutionIntent
    from app.models.live_position import LivePosition
    from app.models.signal import Signal


class LiveOrder(TimestampMixin, Base):
    """Persisted record of one live exchange order attempt."""

    __tablename__ = "live_orders"
    __table_args__ = (
        sa.CheckConstraint("requested_price >= 0 AND requested_price <= 1", name="live_order_requested_price_range"),
        sa.CheckConstraint(
            "filled_price IS NULL OR (filled_price >= 0 AND filled_price <= 1)",
            name="live_order_filled_price_range",
        ),
        sa.CheckConstraint("size_usd >= 0", name="live_order_size_non_negative"),
        sa.CheckConstraint("shares >= 0", name="live_order_shares_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_intent_id: Mapped[int] = mapped_column(
        sa.ForeignKey("execution_intents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    signal_id: Mapped[int] = mapped_column(
        sa.ForeignKey("signals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    market_id: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    side: Mapped[MarketSide] = mapped_column(
        sa.Enum(MarketSide, name="market_side_enum"),
        nullable=False,
    )
    token_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    client_order_id: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True, index=True)
    requested_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    filled_price: Mapped[float | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    size_usd: Mapped[float] = mapped_column(sa.Numeric(12, 2), nullable=False)
    shares: Mapped[float] = mapped_column(sa.Numeric(18, 6), nullable=False)
    status: Mapped[LiveOrderStatus] = mapped_column(
        sa.Enum(LiveOrderStatus, name="live_order_status_enum"),
        nullable=False,
        index=True,
    )
    failure_reason: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    raw_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    execution_intent: Mapped["ExecutionIntent"] = relationship(back_populates="live_orders")
    signal: Mapped["Signal"] = relationship(back_populates="live_orders")
    live_positions: Mapped[list["LivePosition"]] = relationship(back_populates="live_order")
