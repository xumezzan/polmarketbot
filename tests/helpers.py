from app.config import Settings


def build_test_settings(**overrides: object) -> Settings:
    """Return deterministic settings for unit tests."""
    base = {
        "news_fetch_mode": "stub",
        "llm_mode": "stub",
        "market_fetch_mode": "stub",
        "alert_mode": "noop",
        "postgres_host": "127.0.0.1",
    }
    base.update(overrides)
    return Settings(**base)
