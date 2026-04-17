from pydantic import BaseModel, Field


class PriceHistoryPoint(BaseModel):
    """One historical market price datapoint."""

    timestamp: int
    price: float


class BatchPriceHistoryResult(BaseModel):
    """Historical price series keyed by market asset id."""

    history: dict[str, list[PriceHistoryPoint]] = Field(default_factory=dict)
