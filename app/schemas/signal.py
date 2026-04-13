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
    fair_probability: float
    edge: float
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
