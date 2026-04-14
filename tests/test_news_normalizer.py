from datetime import UTC, datetime

from app.schemas.news import NewsApiArticle, NewsApiSource
from app.services.news_normalizer import NewsNormalizer
from tests.helpers import build_test_settings


def _build_article(
    *,
    title: str,
    description: str,
    content: str,
    url: str = "https://example.com/article",
    source_name: str = "Example News",
) -> NewsApiArticle:
    return NewsApiArticle(
        source=NewsApiSource(name=source_name),
        title=title,
        description=description,
        url=url,
        publishedAt=datetime.now(UTC),
        content=content,
    )


def test_news_normalizer_keeps_relevant_crypto_news() -> None:
    normalizer = NewsNormalizer(settings=build_test_settings())
    article = _build_article(
        title="SEC weighs new Bitcoin ETF approval path",
        description="Crypto traders watch the regulator closely.",
        content="Bitcoin and ETF sentiment improved after the SEC update.",
    )

    normalized = normalizer.normalize_article(article)

    assert normalized is not None
    assert normalized.title == "SEC weighs new Bitcoin ETF approval path"


def test_news_normalizer_filters_out_auction_noise() -> None:
    normalizer = NewsNormalizer(settings=build_test_settings())
    article = _build_article(
        title="1957 Chevrolet Bel Air auction result surprises bidders",
        description="The classic car sold above estimate at auction.",
        content="Collectors watched the auction closely.",
    )

    normalized = normalizer.normalize_article(article)

    assert normalized is None


def test_news_normalizer_filters_out_sports_noise() -> None:
    normalizer = NewsNormalizer(settings=build_test_settings())
    article = _build_article(
        title="Blue Jackets vs Bruins preview for tonight",
        description="Hockey fans expect a physical game.",
        content="The Bruins and Blue Jackets meet again tonight.",
    )

    normalized = normalizer.normalize_article(article)

    assert normalized is None
