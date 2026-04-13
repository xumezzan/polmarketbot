from pydantic import BaseModel, Field


class PipelineItemResult(BaseModel):
    """Result of processing one news item through the pipeline."""

    news_item_id: int
    analysis_id: int | None = None
    market_candidate_count: int = 0
    actionable_signal_count: int = 0
    approved_signal_count: int = 0
    blocked_signal_count: int = 0
    opened_position_count: int = 0
    opened_trade_ids: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SchedulerCycleResult(BaseModel):
    """Summary of one scheduler cycle."""

    cycle_id: str
    started_at: str
    finished_at: str
    source_mode: str
    llm_mode: str
    fetch_mode: str
    inserted_news_count: int
    pending_news_count: int
    processed_news_count: int
    actionable_signal_count: int
    approved_signal_count: int
    opened_position_count: int
    error_count: int
    item_results: list[PipelineItemResult] = Field(default_factory=list)
