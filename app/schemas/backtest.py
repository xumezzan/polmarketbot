from pydantic import BaseModel, Field


class BacktestSummary(BaseModel):
    """Top-level signal replay metrics."""

    signals_total: int = 0
    direction_none_skipped_count: int = 0
    missing_candidate_count: int = 0
    missing_token_count: int = 0
    missing_history_count: int = 0
    unresolved_count: int = 0
    signals_scored: int = 0
    resolved_count: int = 0
    win_rate: float = 0.0
    avg_predicted_net_edge: float | None = None
    avg_realized_edge: float | None = None
    avg_raw_brier: float | None = None
    avg_calibrated_brier: float | None = None


class BacktestBucket(BaseModel):
    """Calibration bucket aggregated over resolved rows."""

    bucket: str
    n: int = 0
    avg_raw_probability: float = 0.0
    avg_calibrated_probability: float = 0.0
    empirical_rate: float = 0.0
    raw_brier: float = 0.0
    calibrated_brier: float = 0.0


class BacktestRow(BaseModel):
    """One scored or skipped signal-replay row."""

    signal_id: int
    analysis_id: int
    market_id: str
    created_at: str
    signal_status: str
    direction: str
    raw_probability: float
    calibrated_probability: float
    stored_net_edge: float
    token_id: str | None = None
    entry_price_historical: float | None = None
    resolution_outcome: str | None = None
    outcome_value: float | None = None
    realized_edge: float | None = None
    realized_pnl_per_share: float | None = None
    raw_brier: float | None = None
    calibrated_brier: float | None = None
    hit: bool | None = None
    skip_reason: str | None = None


class BacktestRunResult(BaseModel):
    """Structured output of one signal-replay backtest."""

    generated_at: str
    mode: str
    window_start: str
    window_end: str
    entry_lag_minutes: int
    interval: str
    signal_status_filter: str = "all"
    summary: BacktestSummary
    buckets: list[BacktestBucket] = Field(default_factory=list)
    rows: list[BacktestRow] = Field(default_factory=list)
