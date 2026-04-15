import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.repositories.news_repo import NewsRepository
from app.schemas.news import NewsApiArticle, NewsApiResponse, NewsImportResult
from app.services.news_normalizer import NewsNormalizer
from app.services.retry_utils import retry_async


logger = logging.getLogger(__name__)
_NEWSAPI_COOLDOWN_UNTIL: datetime | None = None
_NEWSAPI_NEXT_ALLOWED_FETCH_AT: datetime | None = None


class NewsClientProtocol(Protocol):
    """Common interface for stub and real news providers."""

    source_mode: str

    async def fetch_latest(self) -> list[NewsApiArticle]:
        """Return the latest article batch."""


class NewsApiError(Exception):
    """Raised when NewsAPI returns an invalid or error response."""


class NewsApiClient:
    """Thin adapter around the official NewsAPI Everything endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source_mode = "newsapi"

    async def fetch_latest(self) -> list[NewsApiArticle]:
        now = datetime.now(UTC)
        cooldown_status = _get_newsapi_cooldown_status(now=now)
        if cooldown_status is not None:
            cooldown_until, remaining_seconds = cooldown_status
            log_event(
                logger,
                "news_api_fetch_skipped_cooldown",
                provider="newsapi",
                cooldown_until=cooldown_until.isoformat(),
                remaining_seconds=remaining_seconds,
            )
            return []

        if not self.settings.news_api_key:
            raise NewsApiError("NEWS_API_KEY is required when NEWS_FETCH_MODE=newsapi")

        min_interval_status = _get_newsapi_min_fetch_interval_status(now=now)
        if min_interval_status is not None:
            next_allowed_at, remaining_seconds = min_interval_status
            log_event(
                logger,
                "news_api_fetch_skipped_min_interval",
                provider="newsapi",
                next_allowed_at=next_allowed_at.isoformat(),
                remaining_seconds=remaining_seconds,
                min_interval_minutes=self.settings.news_min_fetch_interval_minutes,
            )
            return []

        _set_newsapi_next_allowed_fetch(
            now=now,
            min_interval_minutes=self.settings.news_min_fetch_interval_minutes,
        )

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

        async def _request_once() -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=self.settings.news_api_timeout_seconds
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"X-Api-Key": self.settings.news_api_key},
                )
            response.raise_for_status()
            return response

        try:
            response = await retry_async(
                _request_once,
                logger=logger,
                provider="newsapi",
                operation_name="fetch_everything",
                max_attempts=self.settings.news_retry_max_attempts,
                base_delay_seconds=self.settings.news_retry_base_delay_seconds,
                is_retryable=_is_retryable_newsapi_exception,
                context={"lookback_hours": lookback_hours, "page": page},
            )
        except httpx.HTTPError as exc:
            response_text = ""
            if isinstance(exc, httpx.HTTPStatusError):
                response_text = exc.response.text
                if exc.response.status_code == 429:
                    cooldown_until, cooldown_seconds = resolve_newsapi_cooldown_until(
                        now=datetime.now(UTC),
                        retry_after_value=exc.response.headers.get("Retry-After"),
                        fallback_minutes=self.settings.news_rate_limit_cooldown_minutes,
                    )
                    _set_newsapi_cooldown(cooldown_until)
                    log_event(
                        logger,
                        "news_api_rate_limit_cooldown_started",
                        provider="newsapi",
                        cooldown_until=cooldown_until.isoformat(),
                        cooldown_seconds=cooldown_seconds,
                        fallback_minutes=self.settings.news_rate_limit_cooldown_minutes,
                        lookback_hours=lookback_hours,
                    )

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


def _is_retryable_newsapi_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599

    return isinstance(exc, httpx.TransportError)


def resolve_newsapi_cooldown_until(
    *,
    now: datetime,
    retry_after_value: str | None,
    fallback_minutes: int,
) -> tuple[datetime, int]:
    """Return cooldown deadline and duration after a 429 response."""
    cooldown_seconds = _parse_retry_after_seconds(
        retry_after_value=retry_after_value,
        now=now,
    )
    if cooldown_seconds is None:
        cooldown_seconds = max(fallback_minutes * 60, 60)

    return now + timedelta(seconds=cooldown_seconds), cooldown_seconds


def get_newsapi_cooldown_remaining_seconds(
    *,
    cooldown_until: datetime | None,
    now: datetime,
) -> int | None:
    """Return remaining cooldown seconds, or None when cooldown is inactive."""
    if cooldown_until is None:
        return None

    remaining_seconds = int((cooldown_until - now).total_seconds())
    if remaining_seconds <= 0:
        return None
    return remaining_seconds


def resolve_newsapi_next_allowed_fetch_at(
    *,
    now: datetime,
    min_interval_minutes: int,
) -> datetime | None:
    if min_interval_minutes <= 0:
        return None
    return now + timedelta(minutes=min_interval_minutes)


def get_newsapi_fetch_interval_remaining_seconds(
    *,
    next_allowed_at: datetime | None,
    now: datetime,
) -> int | None:
    if next_allowed_at is None:
        return None

    remaining_seconds = int((next_allowed_at - now).total_seconds())
    if remaining_seconds <= 0:
        return None
    return remaining_seconds


def _get_newsapi_cooldown_status(*, now: datetime) -> tuple[datetime, int] | None:
    global _NEWSAPI_COOLDOWN_UNTIL

    remaining_seconds = get_newsapi_cooldown_remaining_seconds(
        cooldown_until=_NEWSAPI_COOLDOWN_UNTIL,
        now=now,
    )
    if remaining_seconds is None:
        _NEWSAPI_COOLDOWN_UNTIL = None
        return None

    return _NEWSAPI_COOLDOWN_UNTIL, remaining_seconds


def _get_newsapi_min_fetch_interval_status(
    *,
    now: datetime,
) -> tuple[datetime, int] | None:
    global _NEWSAPI_NEXT_ALLOWED_FETCH_AT

    remaining_seconds = get_newsapi_fetch_interval_remaining_seconds(
        next_allowed_at=_NEWSAPI_NEXT_ALLOWED_FETCH_AT,
        now=now,
    )
    if remaining_seconds is None:
        _NEWSAPI_NEXT_ALLOWED_FETCH_AT = None
        return None

    return _NEWSAPI_NEXT_ALLOWED_FETCH_AT, remaining_seconds


def _set_newsapi_cooldown(cooldown_until: datetime) -> None:
    global _NEWSAPI_COOLDOWN_UNTIL
    _NEWSAPI_COOLDOWN_UNTIL = cooldown_until


def _set_newsapi_next_allowed_fetch(
    *,
    now: datetime,
    min_interval_minutes: int,
) -> None:
    global _NEWSAPI_NEXT_ALLOWED_FETCH_AT
    _NEWSAPI_NEXT_ALLOWED_FETCH_AT = resolve_newsapi_next_allowed_fetch_at(
        now=now,
        min_interval_minutes=min_interval_minutes,
    )


def _parse_retry_after_seconds(
    *,
    retry_after_value: str | None,
    now: datetime,
) -> int | None:
    if not retry_after_value:
        return None

    stripped_value = retry_after_value.strip()
    if stripped_value.isdigit():
        seconds = int(stripped_value)
        return seconds if seconds > 0 else None

    try:
        retry_after_dt = parsedate_to_datetime(stripped_value)
    except (TypeError, ValueError, IndexError):
        return None

    if retry_after_dt.tzinfo is None:
        retry_after_dt = retry_after_dt.replace(tzinfo=UTC)

    delta_seconds = int((retry_after_dt - now).total_seconds())
    return delta_seconds if delta_seconds > 0 else None


class StubNewsClient:
    """Fake provider for end-to-end local runs without an API key."""

    source_mode = "stub"

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


class RssFeedError(Exception):
    """Raised when RSS feed fetch/parsing fails."""


class RssNewsClient:
    """Lightweight RSS adapter for fallback news ingestion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source_mode = "rss"

    async def fetch_latest(self) -> list[NewsApiArticle]:
        feed_urls = [item.strip() for item in self.settings.rss_feed_urls.split(",") if item.strip()]
        if not feed_urls:
            log_event(
                logger,
                "rss_fetch_skipped_no_feeds",
                provider="rss",
            )
            return []

        all_articles: list[NewsApiArticle] = []
        async with httpx.AsyncClient(timeout=self.settings.rss_request_timeout_seconds) as client:
            for feed_url in feed_urls:
                try:
                    response = await client.get(feed_url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    log_event(
                        logger,
                        "rss_feed_failed",
                        provider="rss",
                        feed_url=feed_url,
                        error=str(exc),
                    )
                    continue

                articles = parse_rss_feed_articles(
                    feed_text=response.text,
                    feed_url=feed_url,
                    max_items=self.settings.rss_max_items_per_feed,
                )
                filtered_articles, blocked_count, allowlist_miss_count = filter_rss_articles_by_source(
                    articles=articles,
                    allowed_sources_csv=self.settings.rss_allowed_sources,
                    blocked_sources_csv=self.settings.rss_blocked_sources,
                )
                log_event(
                    logger,
                    "rss_feed_completed",
                    provider="rss",
                    feed_url=feed_url,
                    fetched_count=len(filtered_articles),
                    raw_count=len(articles),
                    blocked_count=blocked_count,
                    allowlist_miss_count=allowlist_miss_count,
                )
                all_articles.extend(filtered_articles)

        log_event(
            logger,
            "rss_fetch_completed",
            provider="rss",
            feed_count=len(feed_urls),
            fetched_count=len(all_articles),
        )
        return all_articles


class FallbackNewsClient:
    """Try primary client first, then fallback client when needed."""

    def __init__(
        self,
        *,
        primary: NewsClientProtocol,
        fallback: NewsClientProtocol,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.source_mode = getattr(primary, "source_mode", "primary")

    async def fetch_latest(self) -> list[NewsApiArticle]:
        primary_error: Exception | None = None
        try:
            primary_articles = await self.primary.fetch_latest()
        except Exception as exc:
            primary_error = exc
            primary_articles = []
            log_event(
                logger,
                "news_fallback_primary_failed",
                primary_source=getattr(self.primary, "source_mode", "primary"),
                fallback_source=getattr(self.fallback, "source_mode", "fallback"),
                error=str(exc),
            )

        if primary_articles:
            self.source_mode = getattr(self.primary, "source_mode", "primary")
            return primary_articles

        fallback_articles = await self.fallback.fetch_latest()
        if fallback_articles:
            self.source_mode = (
                f"{getattr(self.primary, 'source_mode', 'primary')}->"
                f"{getattr(self.fallback, 'source_mode', 'fallback')}"
            )
            log_event(
                logger,
                "news_fallback_activated",
                primary_source=getattr(self.primary, "source_mode", "primary"),
                fallback_source=getattr(self.fallback, "source_mode", "fallback"),
                fetched_count=len(fallback_articles),
            )
            return fallback_articles

        self.source_mode = getattr(self.primary, "source_mode", "primary")
        if primary_error is not None:
            raise primary_error
        return []


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
        source_mode = getattr(self.client, "source_mode", self.source_mode)
        normalized_result = self.normalizer.normalize_batch(articles)
        inserted_count, db_skipped_count = await self.repository.save_many(
            normalized_result.items
        )

        result = NewsImportResult(
            source_mode=source_mode,
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

    if mode == "rss":
        return RssNewsClient(settings)

    if mode == "newsapi":
        primary = NewsApiClient(settings)
        if settings.news_fallback_mode.lower() == "rss":
            return FallbackNewsClient(
                primary=primary,
                fallback=RssNewsClient(settings),
            )
        return primary

    raise ValueError("Unsupported NEWS_FETCH_MODE. Expected 'stub', 'newsapi' or 'rss'.")


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


def parse_rss_feed_articles(
    *,
    feed_text: str,
    feed_url: str,
    max_items: int,
) -> list[NewsApiArticle]:
    try:
        root = ElementTree.fromstring(feed_text)
    except ElementTree.ParseError as exc:
        raise RssFeedError(f"Invalid RSS/Atom payload: {exc}") from exc

    local_name = _xml_local_name(root.tag)
    if local_name == "rss":
        return _parse_rss_items(root=root, feed_url=feed_url, max_items=max_items)
    if local_name == "feed":
        return _parse_atom_entries(root=root, feed_url=feed_url, max_items=max_items)

    raise RssFeedError(f"Unsupported feed format: root={root.tag}")


def _parse_rss_items(
    *,
    root,
    feed_url: str,
    max_items: int,
) -> list[NewsApiArticle]:
    channel = root.find("channel")
    if channel is None:
        return []

    source_name = _safe_feed_source_name(
        title=_first_text(channel, "title"),
        feed_url=feed_url,
    )
    is_google_news_feed = source_name == "Google News RSS"
    items = channel.findall("item")
    articles: list[NewsApiArticle] = []
    for item in items[: max(max_items, 1)]:
        link = _first_text(item, "link")
        title = _first_text(item, "title")
        if not link or not title:
            continue

        publisher_name = None
        if is_google_news_feed:
            title, publisher_name = _split_google_news_title(title)

        articles.append(
            NewsApiArticle(
                source={"name": publisher_name or source_name},
                title=title,
                description=_strip_html(_first_text(item, "description")),
                url=link,
                publishedAt=_parse_feed_datetime(_first_text(item, "pubDate")),
                content=_strip_html(_first_text(item, "description")),
            )
        )
    return articles


def _parse_atom_entries(
    *,
    root,
    feed_url: str,
    max_items: int,
) -> list[NewsApiArticle]:
    source_name = _safe_feed_source_name(
        title=_first_text(root, "{*}title"),
        feed_url=feed_url,
    )
    entries = root.findall("{*}entry")
    articles: list[NewsApiArticle] = []
    for entry in entries[: max(max_items, 1)]:
        link = _atom_link(entry)
        title = _first_text(entry, "{*}title")
        if not link or not title:
            continue

        description = _strip_html(
            _first_text(entry, "{*}summary") or _first_text(entry, "{*}content")
        )
        published_at = _parse_feed_datetime(
            _first_text(entry, "{*}published") or _first_text(entry, "{*}updated")
        )
        articles.append(
            NewsApiArticle(
                source={"name": source_name},
                title=title,
                description=description,
                url=link,
                publishedAt=published_at,
                content=description,
            )
        )
    return articles


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(element, path: str) -> str | None:
    child = element.find(path)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _atom_link(entry) -> str | None:
    for link in entry.findall("{*}link"):
        href = (link.attrib.get("href") or "").strip()
        rel = (link.attrib.get("rel") or "alternate").strip()
        if href and rel in {"alternate", ""}:
            return href
    return None


def _parse_feed_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _feed_source_name(feed_url: str) -> str:
    hostname = urlparse(feed_url).hostname or "rss"
    return hostname.replace("www.", "")


def _safe_feed_source_name(*, title: str | None, feed_url: str) -> str:
    hostname = _feed_source_name(feed_url)
    lowered_host = hostname.lower()
    normalized_title = (title or "").strip()

    if "news.google.com" in lowered_host:
        return "Google News RSS"

    if normalized_title and len(normalized_title) <= 100:
        return normalized_title

    return hostname[:100]


def _split_google_news_title(title: str) -> tuple[str, str | None]:
    normalized_title = title.strip()
    if " - " not in normalized_title:
        return normalized_title, None

    headline, publisher = normalized_title.rsplit(" - ", 1)
    headline = headline.strip()
    publisher = publisher.strip()

    if not headline or not publisher:
        return normalized_title, None

    if len(publisher) > 100:
        publisher = publisher[:100].rstrip()

    return headline, publisher


def filter_rss_articles_by_source(
    *,
    articles: list[NewsApiArticle],
    allowed_sources_csv: str,
    blocked_sources_csv: str,
) -> tuple[list[NewsApiArticle], int, int]:
    allowed_patterns = _parse_source_patterns(allowed_sources_csv)
    blocked_patterns = _parse_source_patterns(blocked_sources_csv)

    filtered_articles: list[NewsApiArticle] = []
    blocked_count = 0
    allowlist_miss_count = 0

    for article in articles:
        source_name = (article.source.name or "").strip()
        allowed, reason = _is_rss_source_allowed(
            source_name=source_name,
            allowed_patterns=allowed_patterns,
            blocked_patterns=blocked_patterns,
        )
        if allowed:
            filtered_articles.append(article)
            continue

        if reason == "blocked":
            blocked_count += 1
        elif reason == "allowlist_miss":
            allowlist_miss_count += 1

    return filtered_articles, blocked_count, allowlist_miss_count


def _is_rss_source_allowed(
    *,
    source_name: str,
    allowed_patterns: list[str],
    blocked_patterns: list[str],
) -> tuple[bool, str | None]:
    normalized_source = source_name.lower().strip()

    if normalized_source and any(pattern in normalized_source for pattern in blocked_patterns):
        return False, "blocked"

    if allowed_patterns and not any(pattern in normalized_source for pattern in allowed_patterns):
        return False, "allowlist_miss"

    return True, None


def _parse_source_patterns(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


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
