from pydantic import BaseModel, ConfigDict, Field


class Verdict(BaseModel):
    """Structured LLM output used by later signal and risk stages."""

    model_config = ConfigDict(extra="forbid")

    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
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
