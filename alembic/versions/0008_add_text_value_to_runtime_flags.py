"""add text_value to runtime flags

Revision ID: 0008_add_text_value_to_runtime_flags
Revises: 0007_make_forecast_observations_signal_scoped
Create Date: 2026-04-18 20:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008_add_text_value_to_runtime_flags"
down_revision: str | None = "0007_make_forecast_observations_signal_scoped"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_flags",
        sa.Column("text_value", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runtime_flags", "text_value")
