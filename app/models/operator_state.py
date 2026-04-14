from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OperatorState(Base):
    """Singleton table with lightweight scheduler/operator runtime state."""

    __tablename__ = "operator_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_cycle_started_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    last_cycle_finished_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=True,
    )
    last_cycle_fetched_news_count: Mapped[int | None] = mapped_column(
        sa.Integer(),
        nullable=True,
    )
    last_cycle_inserted_news_count: Mapped[int | None] = mapped_column(
        sa.Integer(),
        nullable=True,
    )
    last_cycle_error_count: Mapped[int | None] = mapped_column(
        sa.Integer(),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(
        sa.Text(),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

