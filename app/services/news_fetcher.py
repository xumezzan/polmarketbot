import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.repositories.news_repo import NewsRepository
from app.schemas.news import NewsApiArticle, NewsApiResponse, NewsImportResult
from app.services.news_normalizer import NewsNormalizer


logger = logging.getLogger(__name__)


class NewsClientProtocol(Protocol):
    """Common interface for stub and real news providers."""

    async def fetch_latest(self) -> list[NewsApiArticle]:
        """Return the latest article batch."""


class NewsApiError(Exception):
    """Raised when NewsAPI returns an invalid or error response."""


class NewsApiClient:
    """Thin adapter around the official NewsAPI Everything endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_latest(self) -> list[NewsApiArticle]:
        if not self.settings.news_api_key:
            raise NewsApiError("NEWS_API_KEY is required when NEWS_FETCH_MODE=newsapi")

        lookback_candidates = resolve_news_lookback_hours_sequence(
            primary_hours=self.settings.news_lookback_hours,
            fallback_hours=self.settings.news_fallback_lookback_hours,
            fallback_enabled=self.settings.news_enable_fallback_lookback,
        )

        last_payload: NewsApiResponse | None = None
        for attempt_index, lookback_hours in enumerate(lookback_candidates, start=1):
            payload = await self._fetch_everything(lookback_hours=lookback_hours)
            last_payload = payload

            if payload.articles:
                log_event(
                    logger,
                    "news_api_fetch_completed",
                    provider="newsapi",
                    query=self.settings.news_query,
                    fetched_count=len(payload.articles),
                    total_results=payload.total_results,
                    lookback_hours=lookback_hours,
                    attempt=attempt_index,
                )
                return payload.articles

            if attempt_index < len(lookback_candidates):
                log_event(
                    logger,
                    "news_api_fetch_empty_retrying",
                    provider="newsapi",
                    query=self.settings.news_query,
                    current_lookback_hours=lookback_hours,
                    next_lookback_hours=lookback_candidates[attempt_index],
                )

        log_event(
            logger,
            "news_api_fetch_completed",
            provider="newsapi",
            query=self.settings.news_query,
            fetched_count=0,
            total_results=last_payload.total_results if last_payload is not None else 0,
            lookback_hours=lookback_candidates[-1],
            attempt=len(lookback_candidates),
        )
        return []

    async def _fetch_everything(self, *, lookback_hours: int) -> NewsApiResponse:
        now = datetime.now(UTC)
        from_dt = now - timedelta(hours=lookback_hours)
        url = f"{self.settings.news_api_base_url.rstrip('/')}/everything"

        import random
        page = random.randint(1, self.settings.news_max_pages)

        params = {
            "q": self.settings.news_query,
            "from": from_dt.isoformat(),
            "to": now.isoformat(),
            "language": self.settings.news_language,
            "sortBy": self.settings.news_sort_by,
            "pageSize": self.settings.news_page_size,
            "page": page,
            "searchIn": self.settings.news_search_in,
        }

        log_event(
            logger,
            "news_api_request_params",
            page=page,
            lookback_hours=lookback_hours,
        )
        if self.settings.news_exclude_domains.strip():
            params["excludeDomains"] = self.settings.news_exclude_domains

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.news_api_timeout_seconds
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"X-Api-Key": self.settings.news_api_key},
                )

            response.raise_for_status()
        except httpx.HTTPError as exc:
            response_text = ""
            if isinstance(exc, httpx.HTTPStatusError):
                response_text = exc.response.text

            log_event(
                logger,
                "news_api_fetch_failed",
                provider="newsapi",
                error=str(exc),
                response_text=response_text,
                lookback_hours=lookback_hours,
            )
            raise NewsApiError(f"NewsAPI request failed: {exc}") from exc

        payload = NewsApiResponse.model_validate(response.json())

        if payload.status != "ok":
            raise NewsApiError(
                payload.message or payload.code or "NewsAPI returned an error response"
            )
        return payload


class StubNewsClient:
    """Fake provider for end-to-end local runs without an API key."""

    async def fetch_latest(self) -> list[NewsApiArticle]:
        stub_payload = {
            "status": "ok",
            "totalResults": 3,
            "articles": [
                {
                    "source": {"id": "stub-1", "name": "Stub Crypto Wire"},
                    "author": "Bot Tester",
                    "title": "Bitcoin rallies after ETF rumor spreads",
                    "description": "Traders react to a fresh ETF rumor in early trading.",
                    "url": "https://example.com/markets/bitcoin-rally?utm_source=test",
                    "urlToImage": "https://example.com/images/bitcoin.png",
                    "publishedAt": "2026-04-13T09:00:00Z",
                    "content": "Traders react to a fresh ETF rumor in early trading. [+128 chars]",
                },
                {
                    "source": {"id": "stub-2", "name": "Stub Macro Journal"},
                    "author": "Bot Tester",
                    "title": "Fed official signals slower rate cuts",
                    "description": "Risk assets wobble as a Fed official sounds cautious.",
                    "url": "https://example.com/macro/fed-signals-slower-cuts",
                    "urlToImage": None,
                    "publishedAt": "2026-04-13T10:15:00Z",
                    "content": "Risk assets wobble as a Fed official sounds cautious.",
                },
                {
                    "source": {"id": "stub-1", "name": "Stub Crypto Wire"},
                    "author": "Bot Tester",
                    "title": "Bitcoin rallies after ETF rumor spreads",
                    "description": "Traders react to a fresh ETF rumor in early trading.",
                    "url": "https://example.com/markets/bitcoin-rally",
                    "urlToImage": "https://example.com/images/bitcoin.png",
                    "publishedAt": "2026-04-13T09:00:00Z",
                    "content": "Traders react to a fresh ETF rumor in early trading. [+128 chars]",
                },
            ],
        }

        payload = NewsApiResponse.model_validate(stub_payload)
        log_event(
            logger,
            "news_stub_fetch_completed",
            provider="stub",
            fetched_count=len(payload.articles),
        )
        return payload.articles


class NewsIngestionService:
    """Fetch, normalize and persist news items."""

    def __init__(
        self,
        *,
        client: NewsClientProtocol,
        normalizer: NewsNormalizer,
        repository: NewsRepository,
        source_mode: str,
    ) -> None:
        self.client = client
        self.normalizer = normalizer
        self.repository = repository
        self.source_mode = source_mode

    async def run(self) -> NewsImportResult:
        articles = await self.client.fetch_latest()
        normalized_result = self.normalizer.normalize_batch(articles)
        inserted_count, db_skipped_count = await self.repository.save_many(
            normalized_result.items
        )

        result = NewsImportResult(
            source_mode=self.source_mode,
            fetched_count=len(articles),
            normalized_count=len(normalized_result.items),
            inserted_count=inserted_count,
            filtered_out_count=normalized_result.filtered_out_count,
            skipped_count=(
                normalized_result.invalid_count
                + normalized_result.filtered_out_count
                + normalized_result.duplicate_in_batch_count
                + db_skipped_count
            ),
        )

        log_event(
            logger,
            "news_ingestion_completed",
            source_mode=result.source_mode,
            fetched_count=result.fetched_count,
            normalized_count=result.normalized_count,
            inserted_count=result.inserted_count,
            filtered_out_count=result.filtered_out_count,
            skipped_count=result.skipped_count,
        )
        return result


def build_news_client(settings: Settings) -> NewsClientProtocol:
    """Return either the stub client or the real NewsAPI client."""
    mode = settings.news_fetch_mode.lower()

    if mode == "stub":
        return StubNewsClient()

    if mode == "newsapi":
        return NewsApiClient(settings)

    raise ValueError("Unsupported NEWS_FETCH_MODE. Expected 'stub' or 'newsapi'.")


def resolve_news_lookback_hours_sequence(
    *,
    primary_hours: int,
    fallback_hours: int,
    fallback_enabled: bool,
) -> list[int]:
    """Return the ordered lookback windows NewsAPI should try."""
    if primary_hours <= 0:
        primary_hours = 24

    sequence = [primary_hours]
    if fallback_enabled and fallback_hours > primary_hours:
        sequence.append(fallback_hours)
    return sequence


async def run_news_ingestion(session: AsyncSession, settings: Settings) -> NewsImportResult:
    """Run one news import cycle."""
    repository = NewsRepository(session)
    service = NewsIngestionService(
        client=build_news_client(settings),
        normalizer=NewsNormalizer(settings=settings),
        repository=repository,
        source_mode=settings.news_fetch_mode.lower(),
    )
    return await service.run()


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_news_ingestion(session, settings)
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
