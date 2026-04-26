from pydantic import BaseModel, Field

from app.schemas.market import MarketCandidate


class SignalEvaluation(BaseModel):
    """One evaluated signal candidate."""

    signal_id: int
    analysis_id: int
    news_item_id: int
    market_id: str
    market_question: str
    direction: str
    market_price: float
    execution_price: float | None = None
    raw_fair_probability: float | None = None
    fair_probability: float
    raw_edge: float | None = None
    edge: float
    estimated_fee_rate: float | None = None
    estimated_fee_per_share: float | None = None
    market_consensus_weight: float | None = None
    calibration_sample_count: int | None = None
    signal_status: str
    explanation: str
    candidate: MarketCandidate


class SignalRunResult(BaseModel):
    """Summary of one signal engine run."""

    analysis_id: int
    news_item_id: int
    evaluated_count: int
    actionable_count: int
    watchlist_count: int
    rejected_count: int
    signals: list[SignalEvaluation] = Field(default_factory=list)
