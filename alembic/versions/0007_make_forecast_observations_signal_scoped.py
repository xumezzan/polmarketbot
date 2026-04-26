"""make forecast observations signal scoped

Revision ID: 0007_make_forecast_observations_signal_scoped
Revises: 0006_add_forecasting_and_cost_tracking
Create Date: 2026-04-16 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_make_forecast_observations_signal_scoped"
down_revision: str | None = "0006_add_forecasting_and_cost_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


forecast_observations = sa.table(
    "forecast_observations",
    sa.column("id", sa.Integer()),
    sa.column("signal_id", sa.Integer()),
    sa.column("position_id", sa.Integer()),
    sa.column("resolved_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            forecast_observations.c.id,
            forecast_observations.c.signal_id,
            forecast_observations.c.resolved_at,
        ).order_by(
            forecast_observations.c.signal_id,
            forecast_observations.c.resolved_at.desc(),
            forecast_observations.c.id.desc(),
        )
    ).all()
    keep_signal_ids: set[int] = set()
    duplicate_ids: list[int] = []
    for row in rows:
        signal_id = int(row.signal_id)
        if signal_id in keep_signal_ids:
            duplicate_ids.append(int(row.id))
            continue
        keep_signal_ids.add(signal_id)

    if duplicate_ids:
        bind.execute(
            sa.delete(forecast_observations).where(
                forecast_observations.c.id.in_(duplicate_ids)
            )
        )

    op.drop_constraint(
        op.f("uq_forecast_observations_position_id"),
        "forecast_observations",
        type_="unique",
    )
    op.alter_column(
        "forecast_observations",
        "position_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_unique_constraint(
        op.f("uq_forecast_observations_signal_id"),
        "forecast_observations",
        ["signal_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.delete(forecast_observations).where(
            forecast_observations.c.position_id.is_(None)
        )
    )
    op.drop_constraint(
        op.f("uq_forecast_observations_signal_id"),
        "forecast_observations",
        type_="unique",
    )
    op.alter_column(
        "forecast_observations",
        "position_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_unique_constraint(
        op.f("uq_forecast_observations_position_id"),
        "forecast_observations",
        ["position_id"],
    )
