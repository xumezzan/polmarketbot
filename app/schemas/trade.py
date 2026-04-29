from pydantic import BaseModel, Field

from app.schemas.forecast_observation import ForecastObservationSyncResult


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
    resolution_outcome: str | None = None
    resolved_at: str | None = None


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
    current_edge: float | None = None
    edge_delta: float | None = None
    holding_minutes: float | None = None
    error: str | None = None


class PaperTradeMaintenanceResult(BaseModel):
    """Summary of one paper-trade maintenance cycle."""

    evaluated_positions: int = 0
    closed_positions: int = 0
    skipped_positions: int = 0
    closed_trade_ids: list[int] = Field(default_factory=list)
    closed_results: list["PaperTradeCloseResult"] = Field(default_factory=list)
    decisions: list[PaperTradeAutoCloseDecision] = Field(default_factory=list)
    observation_sync: ForecastObservationSyncResult | None = None


class PaperOpenPositionReportItem(BaseModel):
    """Read-only diagnostics for one open paper position."""

    position_id: int
    trade_id: int | None = None
    signal_id: int
    analysis_id: int | None = None
    news_item_id: int | None = None
    news_title: str | None = None
    news_source: str | None = None
    market_id: str
    market_question: str | None = None
    market_query: str | None = None
    side: str
    entry_price: float
    current_price: float | None = None
    current_price_delta: float | None = None
    size_usd: float
    shares: float
    fair_probability: float | None = None
    entry_edge: float | None = None
    current_edge: float | None = None
    edge_delta: float | None = None
    opened_at: str
    holding_minutes: float
    action: str
    close_reason: str | None = None
    opposite_news_reason: str | None = None
    liquidity: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    last_trade_price: float | None = None
    error: str | None = None


class PaperOpenPositionReport(BaseModel):
    """Read-only diagnostics report for all open paper positions."""

    generated_at: str
    count: int = 0
    would_close_count: int = 0
    held_count: int = 0
    skipped_count: int = 0
    items: list[PaperOpenPositionReportItem] = Field(default_factory=list)


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


class PaperTradeAnalyticsSummary(BaseModel):
    """Period summary for paper-trading analytics."""

    period_days: int | None = None
    opened_trades: int = 0
    closed_trades: int = 0
    current_open_positions: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    expectancy: float = 0.0
    avg_holding_minutes: float = 0.0


class PaperTradeDailyAnalytics(BaseModel):
    """Realized and opened trade counts for one day."""

    date: str
    opened_trades: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0


class PaperTradeBreakdownRow(BaseModel):
    """PnL breakdown grouped by one dimension such as market or source."""

    key: str
    label: str
    trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_holding_minutes: float = 0.0


class PaperRiskBlockerCount(BaseModel):
    """Frequency of one risk blocker over the selected period."""

    blocker: str
    count: int


class ForecastCalibrationBucket(BaseModel):
    """Reliability bucket for resolved forecast probabilities."""

    bucket: str
    count: int = 0
    avg_raw_probability: float = 0.0
    avg_calibrated_probability: float = 0.0
    actual_frequency: float = 0.0
    calibration_error: float = 0.0
    avg_raw_brier: float = 0.0
    avg_calibrated_brier: float = 0.0
    avg_raw_log_loss: float = 0.0
    avg_calibrated_log_loss: float = 0.0


class ForecastCalibrationBreakdown(BaseModel):
    """Calibration metrics grouped by one operator-facing dimension."""

    key: str
    count: int = 0
    avg_calibrated_probability: float = 0.0
    actual_frequency: float = 0.0
    calibration_error: float = 0.0
    avg_calibrated_brier: float = 0.0
    avg_calibrated_log_loss: float = 0.0


class ForecastCalibrationReport(BaseModel):
    """Resolved forecast calibration report for paper-trading validation."""

    generated_at: str
    window_days: int | None = None
    resolved_observations: int = 0
    avg_raw_probability: float | None = None
    avg_calibrated_probability: float | None = None
    actual_frequency: float | None = None
    avg_raw_brier: float | None = None
    avg_calibrated_brier: float | None = None
    avg_raw_log_loss: float | None = None
    avg_calibrated_log_loss: float | None = None
    weighted_calibration_error: float | None = None
    buckets: list[ForecastCalibrationBucket] = Field(default_factory=list)
    by_source: list[ForecastCalibrationBreakdown] = Field(default_factory=list)
    by_model: list[ForecastCalibrationBreakdown] = Field(default_factory=list)
    by_topic: list[ForecastCalibrationBreakdown] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PaperTradeFunnelStats(BaseModel):
    """Conversion funnel from analysis to paper trade."""

    analyses: int = 0
    actionable_signals: int = 0
    approved_signals: int = 0
    blocked_signals: int = 0
    opened_trades: int = 0
    closed_trades: int = 0
    analysis_to_actionable_rate: float = 0.0
    actionable_to_approved_rate: float = 0.0
    approved_to_opened_rate: float = 0.0


class PaperTradeAnalytics(BaseModel):
    """Full paper-trading analytics view for a time period."""

    generated_at: str
    summary: PaperTradeAnalyticsSummary
    funnel: PaperTradeFunnelStats
    daily: list[PaperTradeDailyAnalytics] = Field(default_factory=list)
    by_market: list[PaperTradeBreakdownRow] = Field(default_factory=list)
    by_source: list[PaperTradeBreakdownRow] = Field(default_factory=list)
    risk_blockers: list[PaperRiskBlockerCount] = Field(default_factory=list)


class PaperTradeAuditRow(BaseModel):
    """Operator-facing normalized trade row for proof-of-edge review."""

    trade_id: int
    signal_id: int
    market_id: str
    market_question: str | None = None
    news_source: str | None = None
    opened_at: str
    closed_at: str | None = None
    side: str
    entry_price: float
    exit_price: float | None = None
    size_usd: float
    pnl: float | None = None
    close_reason: str | None = None


class PaperTradeConsistencyVerdict(BaseModel):
    """Compact consistency verdict for phase-gate decisions."""

    status: str
    summary: str
    unstable_days: list[str] = Field(default_factory=list)
    concentration_ratio: float = 0.0


class PaperTradePhaseGateReport(BaseModel):
    """Proof-of-edge decision report for progressing beyond paper trading."""

    generated_at: str
    window_days: int
    required_min_days: int
    required_max_days: int
    required_min_closed_trades: int
    verdict: str
    reasons: list[str] = Field(default_factory=list)
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    closed_trades: int = 0
    analyses: int = 0
    actionable_signals: int = 0
    approved_signals: int = 0
    pipeline_failed_cycles: int = 0
    top_winners: list[PaperTradeAuditRow] = Field(default_factory=list)
    top_losers: list[PaperTradeAuditRow] = Field(default_factory=list)
    risk_blockers: list[PaperRiskBlockerCount] = Field(default_factory=list)
    consistency: PaperTradeConsistencyVerdict
