from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import VerdictDirection

if TYPE_CHECKING:
    from app.models.news import NewsItem
    from app.models.signal import Signal


class Analysis(TimestampMixin, Base):
    """Structured LLM verdict linked to a news item."""

    __tablename__ = "analyses"
    __table_args__ = (
        sa.CheckConstraint("relevance >= 0 AND relevance <= 1", name="relevance_range"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.CheckConstraint(
            "fair_probability >= 0 AND fair_probability <= 1",
            name="fair_probability_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    news_item_id: Mapped[int] = mapped_column(
        sa.ForeignKey("news_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relevance: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    direction: Mapped[VerdictDirection] = mapped_column(
        sa.Enum(VerdictDirection, name="verdict_direction_enum"),
        nullable=False,
    )
    fair_probability: Mapped[float] = mapped_column(sa.Numeric(5, 4), nullable=False)
    market_query: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    reason: Mapped[str] = mapped_column(sa.Text, nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    news_item: Mapped["NewsItem"] = relationship(back_populates="analyses")
    signals: Mapped[list["Signal"]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
    )
