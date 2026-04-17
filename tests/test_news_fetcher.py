import asyncio
from datetime import UTC, datetime

from app.services.news_fetcher import (
    FallbackNewsClient,
    RssNewsClient,
    StubNewsClient,
    filter_rss_articles_by_source,
    get_newsapi_cooldown_remaining_seconds,
    get_newsapi_fetch_interval_remaining_seconds,
    parse_rss_feed_articles,
    resolve_news_lookback_hours_sequence,
    resolve_newsapi_next_allowed_fetch_at,
    resolve_newsapi_cooldown_until,
)
from tests.helpers import build_test_settings


def test_news_fetcher_uses_fallback_lookback_when_enabled() -> None:
    sequence = resolve_news_lookback_hours_sequence(
        primary_hours=24,
        fallback_hours=72,
        fallback_enabled=True,
    )

    assert sequence == [24, 72]


def test_news_fetcher_skips_fallback_when_disabled() -> None:
    sequence = resolve_news_lookback_hours_sequence(
        primary_hours=24,
        fallback_hours=72,
        fallback_enabled=False,
    )

    assert sequence == [24]


def test_newsapi_cooldown_uses_retry_after_seconds_when_present() -> None:
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    cooldown_until, cooldown_seconds = resolve_newsapi_cooldown_until(
        now=now,
        retry_after_value="120",
        fallback_minutes=30,
    )

    assert cooldown_seconds == 120
    assert cooldown_until == datetime(2026, 4, 15, 12, 2, tzinfo=UTC)


def test_newsapi_cooldown_falls_back_to_configured_minutes() -> None:
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    cooldown_until, cooldown_seconds = resolve_newsapi_cooldown_until(
        now=now,
        retry_after_value="not-a-number",
        fallback_minutes=30,
    )

    assert cooldown_seconds == 1800
    assert cooldown_until == datetime(2026, 4, 15, 12, 30, tzinfo=UTC)


def test_newsapi_cooldown_remaining_seconds_returns_none_when_expired() -> None:
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    remaining_seconds = get_newsapi_cooldown_remaining_seconds(
        cooldown_until=datetime(2026, 4, 15, 11, 59, tzinfo=UTC),
        now=now,
    )

    assert remaining_seconds is None


def test_newsapi_next_allowed_fetch_at_uses_min_interval_minutes() -> None:
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    next_allowed_at = resolve_newsapi_next_allowed_fetch_at(
        now=now,
        min_interval_minutes=30,
    )

    assert next_allowed_at == datetime(2026, 4, 15, 12, 30, tzinfo=UTC)


def test_newsapi_fetch_interval_remaining_seconds_returns_none_when_expired() -> None:
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    remaining_seconds = get_newsapi_fetch_interval_remaining_seconds(
        next_allowed_at=datetime(2026, 4, 15, 11, 59, tzinfo=UTC),
        now=now,
    )

    assert remaining_seconds is None


def test_stub_news_client_returns_recent_articles() -> None:
    articles = asyncio.run(StubNewsClient().fetch_latest())

    assert len(articles) == 3
    now = datetime.now(UTC)
    for article in articles:
        age_minutes = (now - article.published_at).total_seconds() / 60
        assert 0 <= age_minutes <= 60


def test_parse_rss_feed_articles_returns_newsapi_articles() -> None:
    feed_text = """
    <rss version="2.0">
      <channel>
        <title>Example Feed</title>
        <item>
          <title>Bitcoin rises on ETF speculation</title>
          <link>https://example.com/bitcoin-etf</link>
          <description><![CDATA[Bitcoin moved higher after renewed ETF chatter.]]></description>
          <pubDate>Tue, 15 Apr 2026 12:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    articles = parse_rss_feed_articles(
        feed_text=feed_text,
        feed_url="https://example.com/rss",
        max_items=10,
    )

    assert len(articles) == 1
    assert articles[0].source.name == "Example Feed"
    assert articles[0].title == "Bitcoin rises on ETF speculation"
    assert articles[0].url == "https://example.com/bitcoin-etf"


def test_parse_rss_feed_articles_extracts_google_news_publisher_from_title() -> None:
    feed_text = """
    <rss version="2.0">
      <channel>
        <title>"bitcoin OR ethereum OR crypto OR ETF OR election" - Google News</title>
        <item>
          <title>Headline - Example Publisher</title>
          <link>https://example.com/story</link>
          <description>Body</description>
        </item>
      </channel>
    </rss>
    """

    articles = parse_rss_feed_articles(
        feed_text=feed_text,
        feed_url="https://news.google.com/rss/search?q=bitcoin",
        max_items=10,
    )

    assert len(articles) == 1
    assert articles[0].source.name == "Example Publisher"
    assert articles[0].title == "Headline"


def test_parse_rss_feed_articles_keeps_google_news_source_when_publisher_missing() -> None:
    feed_text = """
    <rss version="2.0">
      <channel>
        <title>"bitcoin OR ethereum OR crypto OR ETF OR election" - Google News</title>
        <item>
          <title>Headline without publisher suffix</title>
          <link>https://example.com/story</link>
          <description>Body</description>
        </item>
      </channel>
    </rss>
    """

    articles = parse_rss_feed_articles(
        feed_text=feed_text,
        feed_url="https://news.google.com/rss/search?q=bitcoin",
        max_items=10,
    )

    assert len(articles) == 1
    assert articles[0].source.name == "Google News RSS"
    assert articles[0].title == "Headline without publisher suffix"


def test_filter_rss_articles_by_source_blocks_configured_publishers() -> None:
    articles = parse_rss_feed_articles(
        feed_text="""
        <rss version="2.0">
          <channel>
            <title>"bitcoin OR election" - Google News</title>
            <item>
              <title>Headline A - Reuters</title>
              <link>https://example.com/a</link>
              <description>Body</description>
            </item>
            <item>
              <title>Headline B - Cato Institute</title>
              <link>https://example.com/b</link>
              <description>Body</description>
            </item>
          </channel>
        </rss>
        """,
        feed_url="https://news.google.com/rss/search?q=bitcoin",
        max_items=10,
    )

    filtered_articles, blocked_count, allowlist_miss_count = filter_rss_articles_by_source(
        articles=articles,
        allowed_sources_csv="",
        blocked_sources_csv="cato institute",
    )

    assert [article.source.name for article in filtered_articles] == ["Reuters"]
    assert blocked_count == 1
    assert allowlist_miss_count == 0


def test_filter_rss_articles_by_source_respects_allowlist() -> None:
    articles = parse_rss_feed_articles(
        feed_text="""
        <rss version="2.0">
          <channel>
            <title>"bitcoin OR election" - Google News</title>
            <item>
              <title>Headline A - Reuters</title>
              <link>https://example.com/a</link>
              <description>Body</description>
            </item>
            <item>
              <title>Headline B - Example Publisher</title>
              <link>https://example.com/b</link>
              <description>Body</description>
            </item>
          </channel>
        </rss>
        """,
        feed_url="https://news.google.com/rss/search?q=bitcoin",
        max_items=10,
    )

    filtered_articles, blocked_count, allowlist_miss_count = filter_rss_articles_by_source(
        articles=articles,
        allowed_sources_csv="reuters",
        blocked_sources_csv="",
    )

    assert [article.source.name for article in filtered_articles] == ["Reuters"]
    assert blocked_count == 0
    assert allowlist_miss_count == 1


def test_rss_news_client_uses_redirects_and_user_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        text = """
        <rss version="2.0">
          <channel>
            <title>Example Feed</title>
            <item>
              <title>Bitcoin ETF headline</title>
              <link>https://example.com/story</link>
              <description>Body</description>
              <pubDate>Tue, 15 Apr 2026 12:00:00 GMT</pubDate>
            </item>
          </channel>
        </rss>
        """

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str):
            captured["feed_url"] = url
            return FakeResponse()

    monkeypatch.setattr("app.services.news_fetcher.httpx.AsyncClient", FakeAsyncClient)

    settings = build_test_settings(
        news_fetch_mode="rss",
        rss_feed_urls="https://example.com/rss.xml",
        rss_allowed_sources="",
        rss_blocked_sources="",
    )
    articles = asyncio.run(RssNewsClient(settings).fetch_latest())

    assert len(articles) == 1
    assert captured["feed_url"] == "https://example.com/rss.xml"
    assert captured["follow_redirects"] is True
    assert "User-Agent" in captured["headers"]


class _FakeNewsClient:
    def __init__(self, *, source_mode: str, articles, error: Exception | None = None) -> None:
        self.source_mode = source_mode
        self._articles = articles
        self._error = error

    async def fetch_latest(self):
        if self._error is not None:
            raise self._error
        return self._articles


def test_fallback_news_client_uses_fallback_when_primary_empty() -> None:
    primary = _FakeNewsClient(source_mode="newsapi", articles=[])
    fallback_article = {
        "source": {"name": "Example Feed"},
        "title": "ETF headline",
        "url": "https://example.com/etf",
    }
    fallback = _FakeNewsClient(source_mode="rss", articles=[fallback_article])
    client = FallbackNewsClient(primary=primary, fallback=fallback)

    articles = asyncio.run(client.fetch_latest())

    assert len(articles) == 1
    assert client.source_mode == "newsapi->rss"


def test_fallback_news_client_re_raises_primary_error_when_fallback_empty() -> None:
    primary = _FakeNewsClient(
        source_mode="newsapi",
        articles=[],
        error=RuntimeError("newsapi failed"),
    )
    fallback = _FakeNewsClient(source_mode="rss", articles=[])
    client = FallbackNewsClient(primary=primary, fallback=fallback)

    try:
        asyncio.run(client.fetch_latest())
    except RuntimeError as exc:
        assert str(exc) == "newsapi failed"
    else:
        raise AssertionError("Expected RuntimeError from primary client")
