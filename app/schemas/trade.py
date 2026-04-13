from pydantic import BaseModel, Field


class PaperTradeOpenResult(BaseModel):
    """Result of opening one virtual position."""

    signal_id: int
    analysis_id: int
    news_item_id: int
    position_id: int
    trade_id: int
    market_id: str
    side: str
    entry_price: float
    size_usd: float
    shares: float
    status: str
    opened_at: str


class PaperTradeCloseResult(BaseModel):
    """Result of closing one virtual position."""

    signal_id: int
    analysis_id: int
    news_item_id: int
    position_id: int
    trade_id: int
    market_id: str
    side: str
    entry_price: float
    exit_price: float
    size_usd: float
    shares: float
    pnl: float
    status: str
    opened_at: str
    closed_at: str
    close_reason: str | None = None
    holding_minutes: float | None = None
    current_price_delta: float | None = None


class PaperTradeAutoCloseDecision(BaseModel):
    """One auto-close maintenance decision for an open paper position."""

    position_id: int
    trade_id: int | None = None
    signal_id: int
    analysis_id: int | None = None
    news_item_id: int | None = None
    market_id: str
    action: str
    close_reason: str | None = None
    current_price: float | None = None
    current_price_delta: float | None = None
    holding_minutes: float | None = None
    error: str | None = None


class PaperTradeMaintenanceResult(BaseModel):
    """Summary of one paper-trade maintenance cycle."""

    evaluated_positions: int = 0
    closed_positions: int = 0
    skipped_positions: int = 0
    closed_trade_ids: list[int] = Field(default_factory=list)
    decisions: list[PaperTradeAutoCloseDecision] = Field(default_factory=list)


class PaperTradeStats(BaseModel):
    """Aggregated paper-trading metrics."""

    total_trades: int = 0
    closed_trades: int = 0
    open_positions: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    expectancy: float = 0.0
    closed_trade_ids: list[int] = Field(default_factory=list)
