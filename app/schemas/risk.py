from pydantic import BaseModel, Field


class RiskDecision(BaseModel):
    """Deterministic approval or rejection for one signal."""

    signal_id: int
    analysis_id: int
    news_item_id: int
    market_id: str
    allow: bool
    blockers: list[str] = Field(default_factory=list)
    approved_size_usd: float
    signal_status: str
    edge: float
    market_price: float
    fair_probability: float
    checks: dict[str, float | bool | int | str | None] = Field(default_factory=dict)
    evaluated_at: str


class RiskCheckResult(BaseModel):
    """Pure helper result for fake-data verification."""

    allow: bool
    blockers: list[str] = Field(default_factory=list)
    approved_size_usd: float
