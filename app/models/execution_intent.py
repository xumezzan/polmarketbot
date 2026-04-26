from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ExecutionIntentStatus, ExecutionMode, MarketSide

if TYPE_CHECKING:
    from app.models.live_order import LiveOrder
    from app.models.signal import Signal


class ExecutionIntent(TimestampMixin, Base):
    """Shadow/live execution intent persisted before any exchange action."""

    __tablename__ = "execution_intents"
    __table_args__ = (
        sa.CheckConstraint("target_size_usd >= 0", name="execution_intent_target_size_non_negative"),
        sa.CheckConstraint("requested_price >= 0 AND requested_price <= 1", name="execution_intent_requested_price_range"),
        sa.CheckConstraint(
            "max_acceptable_price >= 0 AND max_acceptable_price <= 1",
            name="execution_intent_max_price_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        sa.ForeignKey("signals.id", ondelete="CASCADE"),
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
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        sa.Enum(ExecutionMode, name="execution_mode_enum"),
        nullable=False,
        index=True,
    )
    status: Mapped[ExecutionIntentStatus] = mapped_column(
        sa.Enum(ExecutionIntentStatus, name="execution_intent_status_enum"),
        nullable=False,
        index=True,
    )
    target_size_usd: Mapped[float] = mapped_column(sa.Numeric(12, 2), nullable=False)
    shares: Mapped[float] = mapped_column(sa.Numeric(18, 6), nullable=False)
    requested_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    max_acceptable_price: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    client_order_id: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    generated_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    simulation_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    exchange_order_id: Mapped[str | None] = mapped_column(sa.String(255), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="execution_intents")
    live_orders: Mapped[list["LiveOrder"]] = relationship(back_populates="execution_intent")
