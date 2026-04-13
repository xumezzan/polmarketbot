from datetime import datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class NewsItem(TimestampMixin, Base):
    """Normalized news item stored before any LLM analysis."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    url: Mapped[str] = mapped_column(sa.String(1000), nullable=False, unique=True)
    content: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="news_item",
        cascade="all, delete-orphan",
    )
