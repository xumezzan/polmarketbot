import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_jsonish_list(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return [part.strip() for part in stripped.split(",") if part.strip()]

        if isinstance(parsed, list):
            return parsed
        return [parsed]

    return list(value)


def _parse_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


class GammaMarketEvent(BaseModel):
    """Subset of event fields used for market matching and correlation filtering."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    active: bool | None = None
    closed: bool | None = None


class GammaMarket(BaseModel):
    """Normalized Polymarket Gamma market."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    id: str
    question: str
    slug: str | None = None
    condition_id: str | None = Field(default=None, alias="conditionId")
    description: str | None = None
    active: bool | None = None
    closed: bool | None = None
    archived: bool | None = None
    enable_order_book: bool | None = Field(default=None, alias="enableOrderBook")
    liquidity: float | None = None
    volume: float | None = None
    best_bid: float | None = Field(default=None, alias="bestBid")
    best_ask: float | None = Field(default=None, alias="bestAsk")
    last_trade_price: float | None = Field(default=None, alias="lastTradePrice")
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: list[float] = Field(default_factory=list, alias="outcomePrices")
    clob_token_ids: list[str] = Field(default_factory=list, alias="clobTokenIds")
    events: list[GammaMarketEvent] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("liquidity", "volume", "best_bid", "best_ask", "last_trade_price", mode="before")
    @classmethod
    def parse_numeric_fields(cls, value: Any) -> float | None:
        return _parse_optional_float(value)

    @field_validator("outcomes", "clob_token_ids", mode="before")
    @classmethod
    def parse_string_lists(cls, value: Any) -> list[str]:
        return [str(item) for item in _parse_jsonish_list(value)]

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def parse_price_list(cls, value: Any) -> list[float]:
        return [float(item) for item in _parse_jsonish_list(value)]

    @property
    def primary_event(self) -> GammaMarketEvent | None:
        return self.events[0] if self.events else None

    @property
    def event_id(self) -> str | None:
        return self.primary_event.id if self.primary_event else None

    @property
    def event_slug(self) -> str | None:
        return self.primary_event.slug if self.primary_event else None

    @property
    def event_title(self) -> str | None:
        return self.primary_event.title if self.primary_event else None

    @property
    def yes_price(self) -> float | None:
        return self._price_for_outcome("yes")

    @property
    def no_price(self) -> float | None:
        return self._price_for_outcome("no")

    @property
    def yes_token_id(self) -> str | None:
        return self._token_for_outcome("yes")

    @property
    def no_token_id(self) -> str | None:
        return self._token_for_outcome("no")

    def _price_for_outcome(self, outcome_name: str) -> float | None:
        for outcome, price in zip(self.outcomes, self.outcome_prices, strict=False):
            if outcome.lower() == outcome_name:
                return price
        return None

    def _token_for_outcome(self, outcome_name: str) -> str | None:
        for index, outcome in enumerate(self.outcomes):
            if outcome.lower() == outcome_name and index < len(self.clob_token_ids):
                return self.clob_token_ids[index]
        return None


class MarketCandidate(BaseModel):
    """One ranked market candidate for a given analysis."""

    analysis_id: int
    news_item_id: int
    market_id: str
    question: str
    slug: str | None = None
    condition_id: str | None = None
    event_id: str | None = None
    event_slug: str | None = None
    event_title: str | None = None
    yes_price: float | None = None
    no_price: float | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    last_trade_price: float | None = None
    liquidity: float | None = None
    volume: float | None = None
    match_score: float = Field(ge=0)
    match_reasons: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    correlation_key: str
    raw_market: dict[str, Any]


class MarketMatchResult(BaseModel):
    """Top-N candidate markets for one analysis."""

    analysis_id: int
    news_item_id: int
    market_query: str
    fetch_mode: str
    match_strategy: str
    fetched_count: int
    candidate_count: int
    candidates: list[MarketCandidate]
