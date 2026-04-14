from app.services.news_fetcher import resolve_news_lookback_hours_sequence


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
