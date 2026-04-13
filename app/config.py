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
    signal_actionable_edge_threshold: float = 0.07
    signal_actionable_confidence_threshold: float = 0.70
    signal_actionable_relevance_threshold: float = 0.65
    signal_watchlist_edge_threshold: float = 0.01
    risk_min_confidence: float = 0.70
    risk_min_relevance: float = 0.65
    risk_max_news_age_minutes: int = 360
    risk_min_market_liquidity: float = 10000.0
    risk_priced_in_edge_threshold: float = 0.03
    risk_max_daily_exposure_usd: float = 250.0
    risk_max_trade_size_usd: float = 50.0
    risk_max_liquidity_share: float = 0.02
    risk_block_on_existing_position: bool = True
    paper_require_risk_approval: bool = True
    paper_auto_close_enabled: bool = True
    paper_take_profit_delta: float = 0.08
    paper_stop_loss_delta: float = 0.05
    paper_max_hold_minutes: int = 360
    scheduler_interval_minutes: float = 15.0
    scheduler_news_batch_limit: int = 10
    scheduler_continue_on_item_error: bool = True
    alert_mode: str = "noop"
    alert_on_trade_opened: bool = True
    alert_on_scheduler_error: bool = True
    alert_on_cycle_summary: bool = False
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_request_timeout_seconds: float = 10.0
    telegram_disable_notification: bool = False

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
