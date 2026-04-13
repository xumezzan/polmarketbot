"""use jsonb for raw payload columns"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_use_jsonb_for_raw_payloads"
down_revision: str | None = "0001_create_core_tables"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "news_items",
        "raw_payload",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="raw_payload::jsonb",
        existing_nullable=True,
    )
    op.alter_column(
        "analyses",
        "raw_response",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="raw_response::jsonb",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "raw_response",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        postgresql_using="raw_response::json",
        existing_nullable=True,
    )
    op.alter_column(
        "news_items",
        "raw_payload",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        postgresql_using="raw_payload::json",
        existing_nullable=True,
    )
