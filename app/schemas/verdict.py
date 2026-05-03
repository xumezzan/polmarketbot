from pydantic import BaseModel, ConfigDict, Field


class Verdict(BaseModel):
    """Structured LLM output used by later signal and risk stages."""

    model_config = ConfigDict(extra="forbid")

    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    causality_score: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Direct near-term causal impact on the matching market probability.",
    )
    event_category: str = Field(
        default="OTHER",
        pattern="^(ELECTION|COURT_DECISION|POLITICIAN_HEALTH|WAR_CONFLICT|OTHER)$",
    )
    news_quality: str = Field(default="LOW", pattern="^(CONFIRMED_EVENT|OFFICIAL_STATEMENT|LOW)$")
    direction: str = Field(pattern="^(YES|NO|NONE)$")
    fair_probability: float = Field(ge=0, le=1)
    market_query: str = Field(min_length=3, max_length=255)
    reason: str = Field(min_length=10, max_length=2000)


class AnalysisRunResult(BaseModel):
    """Summary of one LLM analysis run."""

    news_item_id: int
    analysis_id: int
    created_new: bool
    verdict: Verdict
    tradability_score: float | None = None
    market_specificity_score: float | None = None
    market_pipeline_skip_reason: str | None = None
