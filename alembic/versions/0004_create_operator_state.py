"""create operator state table

Revision ID: 0004_create_operator_state
Revises: 0003_create_runtime_flags
Create Date: 2026-04-14 16:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_create_operator_state"
down_revision: str | None = "0003_create_runtime_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_cycle_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cycle_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cycle_fetched_news_count", sa.Integer(), nullable=True),
        sa.Column("last_cycle_inserted_news_count", sa.Integer(), nullable=True),
        sa.Column("last_cycle_error_count", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_state")),
    )


def downgrade() -> None:
    op.drop_table("operator_state")
