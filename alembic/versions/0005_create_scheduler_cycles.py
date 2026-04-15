"""create scheduler cycles table

Revision ID: 0005_create_scheduler_cycles
Revises: 0004_create_operator_state
Create Date: 2026-04-15 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_create_scheduler_cycles"
down_revision: str | None = "0004_create_operator_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduler_cycles",
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_mode", sa.String(length=50), nullable=False),
        sa.Column("llm_mode", sa.String(length=50), nullable=False),
        sa.Column("fetch_mode", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_news_count", sa.Integer(), nullable=True),
        sa.Column("inserted_news_count", sa.Integer(), nullable=True),
        sa.Column("pending_news_count", sa.Integer(), nullable=True),
        sa.Column("processed_news_count", sa.Integer(), nullable=True),
        sa.Column("actionable_signal_count", sa.Integer(), nullable=True),
        sa.Column("approved_signal_count", sa.Integer(), nullable=True),
        sa.Column("opened_position_count", sa.Integer(), nullable=True),
        sa.Column("auto_close_evaluated_count", sa.Integer(), nullable=True),
        sa.Column("closed_position_count", sa.Integer(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("cycle_id", name=op.f("pk_scheduler_cycles")),
    )
    op.create_index(op.f("ix_scheduler_cycles_started_at"), "scheduler_cycles", ["started_at"], unique=False)
    op.create_index(op.f("ix_scheduler_cycles_status"), "scheduler_cycles", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduler_cycles_status"), table_name="scheduler_cycles")
    op.drop_index(op.f("ix_scheduler_cycles_started_at"), table_name="scheduler_cycles")
    op.drop_table("scheduler_cycles")
