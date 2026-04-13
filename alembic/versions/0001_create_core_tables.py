"""create core tables"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_core_tables"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


verdict_direction_enum = sa.Enum("YES", "NO", "NONE", name="verdict_direction_enum")
signal_status_enum = sa.Enum(
    "REJECTED",
    "WATCHLIST",
    "ACTIONABLE",
    name="signal_status_enum",
)
market_side_enum = sa.Enum("YES", "NO", name="market_side_enum")
position_status_enum = sa.Enum("OPEN", "CLOSED", name="position_status_enum")
trade_status_enum = sa.Enum("OPEN", "CLOSED", name="trade_status_enum")


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_items")),
        sa.UniqueConstraint("content_hash", name=op.f("uq_news_items_content_hash")),
        sa.UniqueConstraint("url", name=op.f("uq_news_items_url")),
    )
    op.create_index(op.f("ix_news_items_published_at"), "news_items", ["published_at"], unique=False)

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("news_item_id", sa.Integer(), nullable=False),
        sa.Column("relevance", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("direction", verdict_direction_enum, nullable=False),
        sa.Column("fair_probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("market_query", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name=op.f("ck_analyses_confidence_range")),
        sa.CheckConstraint(
            "fair_probability >= 0 AND fair_probability <= 1",
            name=op.f("ck_analyses_fair_probability_range"),
        ),
        sa.CheckConstraint("relevance >= 0 AND relevance <= 1", name=op.f("ck_analyses_relevance_range")),
        sa.ForeignKeyConstraint(
            ["news_item_id"],
            ["news_items.id"],
            name=op.f("fk_analyses_news_item_id_news_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
    )
    op.create_index(op.f("ix_analyses_news_item_id"), "analyses", ["news_item_id"], unique=False)

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("market_slug", sa.String(length=255), nullable=True),
        sa.Column("market_question", sa.String(length=500), nullable=True),
        sa.Column("market_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("fair_probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("edge", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("signal_status", signal_status_enum, nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("fair_probability >= 0 AND fair_probability <= 1", name=op.f("ck_signals_fair_probability_range")),
        sa.CheckConstraint("market_price >= 0 AND market_price <= 1", name=op.f("ck_signals_market_price_range")),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analyses.id"],
            name=op.f("fk_signals_analysis_id_analyses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
    )
    op.create_index(op.f("ix_signals_analysis_id"), "signals", ["analysis_id"], unique=False)
    op.create_index(op.f("ix_signals_market_id"), "signals", ["market_id"], unique=False)
    op.create_index(op.f("ix_signals_signal_status"), "signals", ["signal_status"], unique=False)

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("market_question", sa.String(length=500), nullable=True),
        sa.Column("side", market_side_enum, nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("shares", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("status", position_status_enum, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("entry_price >= 0 AND entry_price <= 1", name=op.f("ck_positions_entry_price_range")),
        sa.CheckConstraint("shares >= 0", name=op.f("ck_positions_shares_non_negative")),
        sa.CheckConstraint("size_usd >= 0", name=op.f("ck_positions_size_usd_non_negative")),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_positions_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions")),
    )
    op.create_index(op.f("ix_positions_market_id"), "positions", ["market_id"], unique=False)
    op.create_index(op.f("ix_positions_signal_id"), "positions", ["signal_id"], unique=False)
    op.create_index(op.f("ix_positions_status"), "positions", ["status"], unique=False)

    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=True),
        sa.Column("market_id", sa.String(length=255), nullable=False),
        sa.Column("side", market_side_enum, nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("size_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("shares", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("pnl", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("status", trade_status_enum, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("entry_price >= 0 AND entry_price <= 1", name=op.f("ck_paper_trades_entry_price_range")),
        sa.CheckConstraint(
            "exit_price IS NULL OR (exit_price >= 0 AND exit_price <= 1)",
            name=op.f("ck_paper_trades_exit_price_range"),
        ),
        sa.CheckConstraint("shares >= 0", name=op.f("ck_paper_trades_shares_non_negative")),
        sa.CheckConstraint("size_usd >= 0", name=op.f("ck_paper_trades_size_usd_non_negative")),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name=op.f("fk_paper_trades_position_id_positions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signals.id"],
            name=op.f("fk_paper_trades_signal_id_signals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_trades")),
    )
    op.create_index(op.f("ix_paper_trades_market_id"), "paper_trades", ["market_id"], unique=False)
    op.create_index(op.f("ix_paper_trades_position_id"), "paper_trades", ["position_id"], unique=False)
    op.create_index(op.f("ix_paper_trades_signal_id"), "paper_trades", ["signal_id"], unique=False)
    op.create_index(op.f("ix_paper_trades_status"), "paper_trades", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_paper_trades_status"), table_name="paper_trades")
    op.drop_index(op.f("ix_paper_trades_signal_id"), table_name="paper_trades")
    op.drop_index(op.f("ix_paper_trades_position_id"), table_name="paper_trades")
    op.drop_index(op.f("ix_paper_trades_market_id"), table_name="paper_trades")
    op.drop_table("paper_trades")

    op.drop_index(op.f("ix_positions_status"), table_name="positions")
    op.drop_index(op.f("ix_positions_signal_id"), table_name="positions")
    op.drop_index(op.f("ix_positions_market_id"), table_name="positions")
    op.drop_table("positions")

    op.drop_index(op.f("ix_signals_signal_status"), table_name="signals")
    op.drop_index(op.f("ix_signals_market_id"), table_name="signals")
    op.drop_index(op.f("ix_signals_analysis_id"), table_name="signals")
    op.drop_table("signals")

    op.drop_index(op.f("ix_analyses_news_item_id"), table_name="analyses")
    op.drop_table("analyses")

    op.drop_index(op.f("ix_news_items_published_at"), table_name="news_items")
    op.drop_table("news_items")

    trade_status_enum.drop(op.get_bind(), checkfirst=True)
    position_status_enum.drop(op.get_bind(), checkfirst=True)
    market_side_enum.drop(op.get_bind(), checkfirst=True)
    signal_status_enum.drop(op.get_bind(), checkfirst=True)
    verdict_direction_enum.drop(op.get_bind(), checkfirst=True)
