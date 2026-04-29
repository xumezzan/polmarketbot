"""add anomaly hunter tables

Revision ID: 0010_add_anomaly_hunter_tables
Revises: 0009_add_live_execution_tables
Create Date: 2026-04-29 10:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_add_anomaly_hunter_tables"
down_revision: str | None = "0009_add_live_execution_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anomaly_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.String(length=80), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_type", sa.String(length=80), nullable=False),
        sa.Column("subject_type", sa.String(length=50), nullable=True),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_observations")),
    )
    op.create_index(op.f("ix_anomaly_observations_cycle_id"), "anomaly_observations", ["cycle_id"], unique=False)
    op.create_index(op.f("ix_anomaly_observations_observation_type"), "anomaly_observations", ["observation_type"], unique=False)
    op.create_index(op.f("ix_anomaly_observations_observed_at"), "anomaly_observations", ["observed_at"], unique=False)
    op.create_index(op.f("ix_anomaly_observations_severity"), "anomaly_observations", ["severity"], unique=False)
    op.create_index(op.f("ix_anomaly_observations_subject_id"), "anomaly_observations", ["subject_id"], unique=False)
    op.create_index(op.f("ix_anomaly_observations_subject_type"), "anomaly_observations", ["subject_type"], unique=False)

    op.create_table(
        "anomaly_hypotheses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hypothesis_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_hypotheses")),
    )
    op.create_index(op.f("ix_anomaly_hypotheses_generated_at"), "anomaly_hypotheses", ["generated_at"], unique=False)
    op.create_index(op.f("ix_anomaly_hypotheses_hypothesis_type"), "anomaly_hypotheses", ["hypothesis_type"], unique=False)
    op.create_index(op.f("ix_anomaly_hypotheses_score"), "anomaly_hypotheses", ["score"], unique=False)
    op.create_index(op.f("ix_anomaly_hypotheses_status"), "anomaly_hypotheses", ["status"], unique=False)
    op.create_index(op.f("ix_anomaly_hypotheses_window_end"), "anomaly_hypotheses", ["window_end"], unique=False)
    op.create_index(op.f("ix_anomaly_hypotheses_window_start"), "anomaly_hypotheses", ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_anomaly_hypotheses_window_start"), table_name="anomaly_hypotheses")
    op.drop_index(op.f("ix_anomaly_hypotheses_window_end"), table_name="anomaly_hypotheses")
    op.drop_index(op.f("ix_anomaly_hypotheses_status"), table_name="anomaly_hypotheses")
    op.drop_index(op.f("ix_anomaly_hypotheses_score"), table_name="anomaly_hypotheses")
    op.drop_index(op.f("ix_anomaly_hypotheses_hypothesis_type"), table_name="anomaly_hypotheses")
    op.drop_index(op.f("ix_anomaly_hypotheses_generated_at"), table_name="anomaly_hypotheses")
    op.drop_table("anomaly_hypotheses")

    op.drop_index(op.f("ix_anomaly_observations_subject_type"), table_name="anomaly_observations")
    op.drop_index(op.f("ix_anomaly_observations_subject_id"), table_name="anomaly_observations")
    op.drop_index(op.f("ix_anomaly_observations_severity"), table_name="anomaly_observations")
    op.drop_index(op.f("ix_anomaly_observations_observed_at"), table_name="anomaly_observations")
    op.drop_index(op.f("ix_anomaly_observations_observation_type"), table_name="anomaly_observations")
    op.drop_index(op.f("ix_anomaly_observations_cycle_id"), table_name="anomaly_observations")
    op.drop_table("anomaly_observations")
