from app.config import Settings


def build_test_settings(**overrides: object) -> Settings:
    """Return deterministic settings for unit tests."""
    base = {
        "news_fetch_mode": "stub",
        "llm_mode": "stub",
        "llm_openai_fallback_mode": "stub",
        "market_fetch_mode": "stub",
        "alert_mode": "noop",
        "postgres_host": "127.0.0.1",
        "risk_enable_extended_news_age_window": False,
    }
    base.update(overrides)
    return Settings(**base)
