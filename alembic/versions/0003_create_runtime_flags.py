"""create runtime flags table

Revision ID: 0003_create_runtime_flags
Revises: 0002_use_jsonb_for_raw_payloads
Create Date: 2026-04-14 15:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_create_runtime_flags"
down_revision: str | None = "0002_use_jsonb_for_raw_payloads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_flags",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column(
            "bool_value",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_runtime_flags")),
    )


def downgrade() -> None:
    op.drop_table("runtime_flags")

