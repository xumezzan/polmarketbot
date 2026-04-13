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
