from pydantic import BaseModel, Field


class BlockerStat(BaseModel):
    """One blocker reason with count for the report window."""

    reason: str
    count: int


class ProviderFailureStat(BaseModel):
    """One provider failure bucket with count for the report window."""

    provider: str
    count: int


class ProviderCooldownStat(BaseModel):
    """One inferred active provider cooldown."""

    provider: str
    cooldown_until: str
    remaining_seconds: int
    reason: str


class DailyReport(BaseModel):
    """Daily operational summary for the paper-trading bot."""

    generated_at: str
    window_start: str
    window_end: str
    fetched_news_24h: int | None = None
    scheduler_cycles_24h: int = 0
    failed_cycles_24h: int = 0
    consecutive_failed_cycles: int = 0
    consecutive_idle_cycles: int = 0
    last_successful_cycle_at: str | None = None
    inserted_news_24h: int = 0
    analyses_count_24h: int = 0
    llm_tokens_24h: int = 0
    llm_cost_24h: float = 0.0
    signals_count_24h: int = 0
    actionable_signals_count_24h: int = 0
    approved_signals_count_24h: int = 0
    opened_paper_trades_24h: int = 0
    closed_paper_trades_24h: int = 0
    open_positions_count: int = 0
    realized_pnl_24h: float = 0.0
    unrealized_pnl: float | None = None
    unrealized_positions_valued: int = 0
    unrealized_positions_total: int = 0
    provider_failures: list[ProviderFailureStat] = Field(default_factory=list)
    provider_cooldowns: list[ProviderCooldownStat] = Field(default_factory=list)
    top_blockers: list[BlockerStat] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
