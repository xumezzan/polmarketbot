from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NewsApiSource(BaseModel):
    """Source object nested inside a NewsAPI article."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None


class NewsApiArticle(BaseModel):
    """Article shape returned by NewsAPI /v2/everything."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    source: NewsApiSource
    author: str | None = None
    title: str | None = None
    description: str | None = None
    url: str
    url_to_image: str | None = Field(default=None, alias="urlToImage")
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    content: str | None = None


class NewsApiResponse(BaseModel):
    """Top-level NewsAPI response."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    status: Literal["ok", "error"]
    total_results: int | None = Field(default=None, alias="totalResults")
    articles: list[NewsApiArticle] = Field(default_factory=list)
    code: str | None = None
    message: str | None = None


class NormalizedNewsItem(BaseModel):
    """Clean article ready to be stored in news_items."""

    source: str
    title: str
    url: str
    content: str | None = None
    published_at: datetime | None = None
    content_hash: str
    raw_payload: dict[str, Any]


class NormalizationResult(BaseModel):
    """Normalization output with counters for skipped rows."""

    items: list[NormalizedNewsItem]
    invalid_count: int = 0
    duplicate_in_batch_count: int = 0
    filtered_out_count: int = 0


class NewsImportResult(BaseModel):
    """Summary of one ingestion run."""

    source_mode: str
    fetched_count: int
    normalized_count: int
    inserted_count: int
    skipped_count: int
    filtered_out_count: int = 0
