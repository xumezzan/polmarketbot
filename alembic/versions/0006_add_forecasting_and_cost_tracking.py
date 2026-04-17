"""add forecasting and cost tracking

Revision ID: 0006_add_forecasting_and_cost_tracking
Revises: 0005_create_scheduler_cycles
Create Date: 2026-04-16 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_add_forecasting_and_cost_tracking"
down_revision: str | None = "0005_create_scheduler_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("llm_provider", sa.String(length=50), nullable=True))
    op.add_column("analyses", sa.Column("llm_model", sa.String(length=100), nullable=True))
    op.add_column("analyses", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("total_tokens", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True))

    op.add_column("signals", sa.Column("execution_price", sa.Numeric(precision=5, scale=4), nullable=True))
    op.add_column("signals", sa.Column("raw_fair_probability", sa.Numeric(precision=5, scale=4), nullable=True))
    op.add_column("signals", sa.Column("raw_edge", sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column("signals", sa.Column("estimated_fee_rate", sa.Numeric(precision=8, scale=6), nullable=True))
    op.add_column("signals", sa.Column("estimated_fee_per_share", sa.Numeric(precision=8, scale=6), nullable=True))
    op.add_column("signals", sa.Column("market_consensus_weight", sa.Numeric(precision=5, scale=4), nullable=True))
    op.add_column("signals", sa.Column("calibration_sample_count", sa.Integer(), nullable=True))

    op.add_column("positions", sa.Column("close_reason", sa.String(length=255), nullable=True))
    op.add_column("positions", sa.Column("resolution_outcome", sa.String(length=20), nullable=True))
    op.add_column("positions", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("paper_trades", sa.Column("close_reason", sa.String(length=255), nullable=True))
    op.add_column("paper_trades", sa.Column("resolution_outcome", sa.String(length=20), nullable=True))
    op.add_column("paper_trades", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "forecast_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("raw_probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("calibrated_probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("market_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("execution_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("outcome_value", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("outcome_label", sa.String(length=20), nullable=True),
        sa.Column("brier_score", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "raw_probability >= 0 AND raw_probability <= 1",
            name=op.f("ck_forecast_observations_raw_probability_range"),
        ),
        sa.CheckConstraint(
            "calibrated_probability >= 0 AND calibrated_probability <= 1",
            name=op.f("ck_forecast_observations_calibrated_probability_range"),
        ),
        sa.CheckConstraint(
            "market_price >= 0 AND market_price <= 1",
            name=op.f("ck_forecast_observations_market_price_range"),
        ),
        sa.CheckConstraint(
            "execution_price >= 0 AND execution_price <= 1",
            name=op.f("ck_forecast_observations_execution_price_range"),
        ),
        sa.CheckConstraint(
            "outcome_value >= 0 AND outcome_value <= 1",
            name=op.f("ck_forecast_observations_outcome_value_range"),
        ),
        sa.CheckConstraint(
            "brier_score >= 0 AND brier_score <= 1",
            name=op.f("ck_forecast_observations_brier_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.id"],
            name=op.f("fk_forecast_observations_analysis_id_analyses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_forecast_observations_position_id_positions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_forecast_observations_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecast_observations")),
        sa.UniqueConstraint("position_id", name=op.f("uq_forecast_observations_position_id")),
    )
    op.create_index(
        op.f("ix_forecast_observations_analysis_id"),
        "forecast_observations",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_market_id"),
        "forecast_observations",
        ["market_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_model"),
        "forecast_observations",
        ["model"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_position_id"),
        "forecast_observations",
        ["position_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_provider"),
        "forecast_observations",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_resolved_at"),
        "forecast_observations",
        ["resolved_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_side"),
        "forecast_observations",
        ["side"],
        unique=False,
    )
    op.create_index(
        op.f("ix_forecast_observations_signal_id"),
        "forecast_observations",
        ["signal_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_forecast_observations_signal_id"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_side"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_resolved_at"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_provider"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_position_id"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_model"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_market_id"), table_name="forecast_observations")
    op.drop_index(op.f("ix_forecast_observations_analysis_id"), table_name="forecast_observations")
    op.drop_table("forecast_observations")

    op.drop_column("paper_trades", "resolved_at")
    op.drop_column("paper_trades", "resolution_outcome")
    op.drop_column("paper_trades", "close_reason")

    op.drop_column("positions", "resolved_at")
    op.drop_column("positions", "resolution_outcome")
    op.drop_column("positions", "close_reason")

    op.drop_column("signals", "calibration_sample_count")
    op.drop_column("signals", "market_consensus_weight")
    op.drop_column("signals", "estimated_fee_per_share")
    op.drop_column("signals", "estimated_fee_rate")
    op.drop_column("signals", "raw_edge")
    op.drop_column("signals", "raw_fair_probability")
    op.drop_column("signals", "execution_price")

    op.drop_column("analyses", "estimated_cost_usd")
    op.drop_column("analyses", "total_tokens")
    op.drop_column("analyses", "completion_tokens")
    op.drop_column("analyses", "prompt_tokens")
    op.drop_column("analyses", "llm_model")
    op.drop_column("analyses", "llm_provider")
