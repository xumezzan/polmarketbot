from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SchedulerCycle(Base, TimestampMixin):
    """Historical record for one scheduler cycle."""

    __tablename__ = "scheduler_cycles"

    cycle_id: Mapped[str] = mapped_column(sa.String(length=64), primary_key=True)
    status: Mapped[str] = mapped_column(sa.String(length=20), nullable=False, default="STARTED")
    source_mode: Mapped[str] = mapped_column(sa.String(length=50), nullable=False)
    llm_mode: Mapped[str] = mapped_column(sa.String(length=50), nullable=False)
    fetch_mode: Mapped[str] = mapped_column(sa.String(length=50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    fetched_news_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    inserted_news_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    pending_news_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    processed_news_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    actionable_signal_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    approved_signal_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    opened_position_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    auto_close_evaluated_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    closed_position_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    error_count: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
