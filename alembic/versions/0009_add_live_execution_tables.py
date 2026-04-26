"""add live execution tables

Revision ID: 0009_add_live_execution_tables
Revises: 0008_add_text_value_to_runtime_flags
Create Date: 2026-04-24 13:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009_add_live_execution_tables"
down_revision: str | None = "0008_add_text_value_to_runtime_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


execution_mode_enum = postgresql.ENUM(
    "PAPER",
    "SHADOW",
    "LIVE",
    name="execution_mode_enum",
    create_type=False,
)
execution_intent_status_enum = postgresql.ENUM(
    "SIMULATED",
    "SUBMITTED",
    "FAILED",
    "CANCELED",
    name="execution_intent_status_enum",
    create_type=False,
)
live_order_status_enum = postgresql.ENUM(
    "OPEN",
    "FILLED",
    "CANCELED",
    "FAILED",
    name="live_order_status_enum",
    create_type=False,
)
live_position_status_enum = postgresql.ENUM(
    "OPEN",
    "CLOSED",
    name="live_position_status_enum",
    create_type=False,
)
reconciliation_status_enum = postgresql.ENUM(
    "PASSED",
    "MISMATCH",
    "FAILED",
    name="reconciliation_status_enum",
    create_type=False,
)
market_side_enum = postgresql.ENUM("YES", "NO", name="market_side_enum", create_type=False)


def upgrade() -> None:
    execution_mode_enum.create(op.get_bind(), checkfirst=True)
    execution_intent_status_enum.create(op.get_bind(), checkfirst=True)
    live_order_status_enum.create(op.get_bind(), checkfirst=True)
    live_position_status_enum.create(op.get_bind(), checkfirst=True)
    reconciliation_status_enum.create(op.get_bind(), checkfirst=True)
    market_side_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "execution_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("market_question", sa.String(length=500), nullable=True),
        sa.Column("side", market_side_enum, nullable=False),
        sa.Column("token_id", sa.String(length=255), nullable=False),
        sa.Column("execution_mode", execution_mode_enum, nullable=False),
        sa.Column("status", execution_intent_status_enum, nullable=False),
        sa.Column("target_size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("shares", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("requested_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("max_acceptable_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("client_order_id", sa.String(length=120), nullable=False),
        sa.Column("generated_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("simulation_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exchange_order_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "max_acceptable_price >= 0 AND max_acceptable_price <= 1",
            name=op.f("ck_execution_intents_execution_intent_max_price_range"),
        ),
        sa.CheckConstraint(
            "requested_price >= 0 AND requested_price <= 1",
            name=op.f("ck_execution_intents_execution_intent_requested_price_range"),
        ),
        sa.CheckConstraint(
            "target_size_usd >= 0",
            name=op.f("ck_execution_intents_execution_intent_target_size_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_execution_intents_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_intents")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_execution_intents_client_order_id")),
    )
    op.create_index(op.f("ix_execution_intents_execution_mode"), "execution_intents", ["execution_mode"], unique=False)
    op.create_index(op.f("ix_execution_intents_exchange_order_id"), "execution_intents", ["exchange_order_id"], unique=False)
    op.create_index(op.f("ix_execution_intents_market_id"), "execution_intents", ["market_id"], unique=False)
    op.create_index(op.f("ix_execution_intents_signal_id"), "execution_intents", ["signal_id"], unique=False)
    op.create_index(op.f("ix_execution_intents_status"), "execution_intents", ["status"], unique=False)

    op.create_table(
        "live_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_intent_id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("side", market_side_enum, nullable=False),
        sa.Column("token_id", sa.String(length=255), nullable=False),
        sa.Column("client_order_id", sa.String(length=120), nullable=False),
        sa.Column("exchange_order_id", sa.String(length=255), nullable=True),
        sa.Column("requested_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("filled_price", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("shares", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("status", live_order_status_enum, nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("raw_request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "filled_price IS NULL OR (filled_price >= 0 AND filled_price <= 1)",
            name=op.f("ck_live_orders_live_order_filled_price_range"),
        ),
        sa.CheckConstraint(
            "requested_price >= 0 AND requested_price <= 1",
            name=op.f("ck_live_orders_live_order_requested_price_range"),
        ),
        sa.CheckConstraint("shares >= 0", name=op.f("ck_live_orders_live_order_shares_non_negative")),
        sa.CheckConstraint("size_usd >= 0", name=op.f("ck_live_orders_live_order_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["execution_intent_id"],
            ["execution_intents.id"],
            name=op.f("fk_live_orders_execution_intent_id_execution_intents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_live_orders_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_live_orders")),
        sa.UniqueConstraint("client_order_id", name=op.f("uq_live_orders_client_order_id")),
    )
    op.create_index(op.f("ix_live_orders_exchange_order_id"), "live_orders", ["exchange_order_id"], unique=False)
    op.create_index(op.f("ix_live_orders_execution_intent_id"), "live_orders", ["execution_intent_id"], unique=False)
    op.create_index(op.f("ix_live_orders_market_id"), "live_orders", ["market_id"], unique=False)
    op.create_index(op.f("ix_live_orders_signal_id"), "live_orders", ["signal_id"], unique=False)
    op.create_index(op.f("ix_live_orders_status"), "live_orders", ["status"], unique=False)

    op.create_table(
        "live_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("live_order_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("market_question", sa.String(length=500), nullable=True),
        sa.Column("side", market_side_enum, nullable=False),
        sa.Column("token_id", sa.String(length=255), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("shares", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("status", live_position_status_enum, nullable=False),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "entry_price >= 0 AND entry_price <= 1",
            name=op.f("ck_live_positions_live_position_entry_price_range"),
        ),
        sa.CheckConstraint("shares >= 0", name=op.f("ck_live_positions_live_position_shares_non_negative")),
        sa.CheckConstraint("size_usd >= 0", name=op.f("ck_live_positions_live_position_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["live_order_id"],
            ["live_orders.id"],
            name=op.f("fk_live_positions_live_order_id_live_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_live_positions_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_live_positions")),
    )
    op.create_index(op.f("ix_live_positions_live_order_id"), "live_positions", ["live_order_id"], unique=False)
    op.create_index(op.f("ix_live_positions_market_id"), "live_positions", ["market_id"], unique=False)
    op.create_index(op.f("ix_live_positions_signal_id"), "live_positions", ["signal_id"], unique=False)
    op.create_index(op.f("ix_live_positions_status"), "live_positions", ["status"], unique=False)

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", reconciliation_status_enum, nullable=False),
        sa.Column("mismatch_count", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_runs")),
    )
    op.create_index(op.f("ix_reconciliation_runs_status"), "reconciliation_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reconciliation_runs_status"), table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")

    op.drop_index(op.f("ix_live_positions_status"), table_name="live_positions")
    op.drop_index(op.f("ix_live_positions_signal_id"), table_name="live_positions")
    op.drop_index(op.f("ix_live_positions_market_id"), table_name="live_positions")
    op.drop_index(op.f("ix_live_positions_live_order_id"), table_name="live_positions")
    op.drop_table("live_positions")

    op.drop_index(op.f("ix_live_orders_status"), table_name="live_orders")
    op.drop_index(op.f("ix_live_orders_signal_id"), table_name="live_orders")
    op.drop_index(op.f("ix_live_orders_market_id"), table_name="live_orders")
    op.drop_index(op.f("ix_live_orders_execution_intent_id"), table_name="live_orders")
    op.drop_index(op.f("ix_live_orders_exchange_order_id"), table_name="live_orders")
    op.drop_table("live_orders")

    op.drop_index(op.f("ix_execution_intents_status"), table_name="execution_intents")
    op.drop_index(op.f("ix_execution_intents_signal_id"), table_name="execution_intents")
    op.drop_index(op.f("ix_execution_intents_market_id"), table_name="execution_intents")
    op.drop_index(op.f("ix_execution_intents_exchange_order_id"), table_name="execution_intents")
    op.drop_index(op.f("ix_execution_intents_execution_mode"), table_name="execution_intents")
    op.drop_table("execution_intents")

    reconciliation_status_enum.drop(op.get_bind(), checkfirst=True)
    live_position_status_enum.drop(op.get_bind(), checkfirst=True)
    live_order_status_enum.drop(op.get_bind(), checkfirst=True)
    execution_intent_status_enum.drop(op.get_bind(), checkfirst=True)
    execution_mode_enum.drop(op.get_bind(), checkfirst=True)
