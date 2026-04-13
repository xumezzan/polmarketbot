from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "Polymarket News Bot"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_echo: bool = False
    log_level: str = "INFO"

    postgres_db: str = "polymarket"
    postgres_user: str = "polymarket"
    postgres_password: str = "polymarket"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    news_fetch_mode: str = "stub"
    news_api_base_url: str = "https://newsapi.org/v2"
    news_query: str = "bitcoin OR ethereum OR crypto"
    news_language: str = "en"
    news_page_size: int = 20
    news_lookback_hours: int = 24
    news_search_in: str = "title,description,content"
    news_api_timeout_seconds: float = 15.0
    llm_mode: str = "stub"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    openai_temperature: float = 0.0
    openai_max_completion_tokens: int = 300
    llm_max_content_chars: int = 4000
    news_api_key: str = ""
    market_fetch_mode: str = "stub"
    gamma_api_base_url: str = "https://gamma-api.polymarket.com"
    gamma_markets_page_size: int = 200
    gamma_markets_max_pages: int = 5
    gamma_fetch_active_only: bool = True
    gamma_fetch_closed: bool = False
    gamma_request_timeout_seconds: float = 20.0
    market_top_n: int = 5
    market_match_strategy: str = "keyword"
    market_match_min_score: float = 0.15
    market_match_exact_weight: float = 0.35
    market_match_question_weight: float = 0.35
    market_match_slug_weight: float = 0.15
    market_match_event_weight: float = 0.10
    market_match_liquidity_weight: float = 0.05
    market_correlation_filter_enabled: bool = True
    market_correlation_jaccard_threshold: float = 0.75
    market_correlation_block_same_event: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for PostgreSQL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_sync_url(self) -> str:
        """Sync SQLAlchemy URL used by Alembic migrations."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings so they are created only once per process."""
    return Settings()
