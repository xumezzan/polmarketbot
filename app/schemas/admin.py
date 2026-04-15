from pydantic import BaseModel, Field

from app.schemas.trade import PaperTradeStats


class KillSwitchStatus(BaseModel):
    """Response payload for paper-trading kill switch state."""

    enabled: bool
    key: str
    updated_at: str | None = None


class AdminStatusResponse(BaseModel):
    """High-level operator status snapshot."""

    api_alive: bool = True
    generated_at: str
    last_scheduler_cycle_started_at: str | None = None
    last_scheduler_cycle_finished_at: str | None = None
    last_scheduler_cycle_fetched_news_count: int | None = None
    last_scheduler_cycle_inserted_news_count: int | None = None
    last_scheduler_cycle_error_count: int | None = None
    last_error: str | None = None
    news_items_count: int = 0
    analyses_count: int = 0
    signals_count: int = 0
    paper_trades_count: int = 0
    open_positions_count: int = 0
    kill_switch_enabled: bool = False
    fetched_news_24h: int = 0
    scheduler_cycles_24h: int = 0
    failed_cycles_24h: int = 0
    provider_cooldowns: dict[str, dict[str, object]] = Field(default_factory=dict)
    inserted_news_24h: int = 0
    analyses_count_24h: int = 0
    signals_count_24h: int = 0
    opened_trades_24h: int = 0
    closed_trades_24h: int = 0


class RecentSignalItem(BaseModel):
    """One signal row for operator recent view."""

    signal_id: int
    created_at: str
    analysis_id: int
    news_item_id: int
    market_id: str
    market_question: str | None = None
    signal_status: str
    edge: float
    market_price: float
    fair_probability: float
    explanation: str


class RecentSignalsResponse(BaseModel):
    """Recent signal list response."""

    generated_at: str
    limit: int
    count: int
    items: list[RecentSignalItem] = Field(default_factory=list)


class OpenPositionItem(BaseModel):
    """One open position row for operator view."""

    position_id: int
    signal_id: int
    analysis_id: int | None = None
    news_item_id: int | None = None
    market_id: str
    market_question: str | None = None
    side: str
    entry_price: float
    size_usd: float
    shares: float
    opened_at: str
    holding_minutes: float


class OpenPositionsResponse(BaseModel):
    """Open positions response payload."""

    generated_at: str
    count: int
    items: list[OpenPositionItem] = Field(default_factory=list)


class AdminPaperStatsResponse(BaseModel):
    """Paper-trading summary for operator view."""

    generated_at: str
    stats: PaperTradeStats
