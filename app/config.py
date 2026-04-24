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
    news_query: str = (
        '(bitcoin OR ethereum OR crypto OR ETF OR "Federal Reserve" '
        'OR inflation OR tariff OR recession OR election) '
        'NOT (auction OR "No Reserve" OR "Bring a Trailer")'
    )
    news_language: str = "en"
    news_page_size: int = 20
    news_max_pages: int = 5
    news_lookback_hours: int = 24
    news_min_fetch_interval_minutes: int = 30
    news_enable_fallback_lookback: bool = True
    news_fallback_lookback_hours: int = 72
    news_search_in: str = "title,description"
    news_sort_by: str = "relevancy"
    news_exclude_domains: str = "bringatrailer.com"
    news_api_timeout_seconds: float = 15.0
    news_relevance_filter_enabled: bool = True
    news_relevance_min_hits: int = 2
    news_rate_limit_cooldown_minutes: int = 30
    news_fallback_mode: str = ""
    llm_mode: str = "stub"
    llm_openai_fallback_mode: str = "stub"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    openai_temperature: float = 0.0
    openai_max_completion_tokens: int = 300
    openai_input_cost_per_1m_tokens: float = 0.15
    openai_output_cost_per_1m_tokens: float = 0.60
    openai_daily_budget_usd: float = 0.0
    llm_max_content_chars: int = 4000
    news_api_key: str = ""
    rss_feed_urls: str = ""
    rss_allowed_sources: str = ""
    rss_blocked_sources: str = ""
    rss_request_timeout_seconds: float = 15.0
    rss_max_items_per_feed: int = 20
    market_fetch_mode: str = "stub"
    gamma_api_base_url: str = "https://gamma-api.polymarket.com"
    clob_api_base_url: str = "https://clob.polymarket.com"
    clob_chain_id: int = 137
    clob_private_key: str = ""
    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""
    clob_funder: str = ""
    clob_signature_type: int = 0
    execution_mode: str = "paper"
    live_trading_enabled: bool = False
    live_max_trade_size_usd: float = 5.0
    live_min_trade_size_usd: float = 2.0
    live_max_daily_exposure_usd: float = 25.0
    live_max_open_positions: int = 1
    live_order_type: str = "FOK"
    live_price_buffer_bps: float = 50.0
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
    signal_calibration_enabled: bool = True
    signal_calibration_min_samples: int = 5
    signal_calibration_bucket_size: float = 0.10
    signal_calibration_prior_strength: float = 3.0
    signal_market_consensus_liquidity_cap: float = 250000.0
    signal_market_consensus_max_weight: float = 0.35
    signal_liquidity_penalty_factor: float = 0.20
    signal_liquidity_penalty_cap: float = 0.03
    risk_min_confidence: float = 0.70
    risk_min_relevance: float = 0.65
    risk_max_news_age_minutes: int = 360
    risk_enable_extended_news_age_window: bool = False
    risk_extended_max_news_age_minutes: int = 1800
    risk_extended_news_age_size_multiplier: float = 0.5
    risk_min_market_liquidity: float = 10000.0
    risk_priced_in_edge_threshold: float = 0.03
    risk_min_match_score: float = 0.35
    risk_min_query_market_token_overlap: int = 2
    risk_min_query_market_overlap_token_length: int = 5
    risk_min_anchor_entity_overlap: int = 1
    risk_anchor_entity_max_tokens: int = 2
    risk_max_open_positions_per_entity: int = 1
    risk_max_entity_open_exposure_usd: float = 50.0
    risk_max_trades_per_analysis: int = 1
    risk_min_top_candidate_score_delta: float = 0.05
    risk_max_bid_ask_spread: float = 0.03
    risk_max_yes_entry_slippage: float = 0.02
    risk_max_daily_exposure_usd: float = 250.0
    risk_max_trade_size_usd: float = 50.0
    risk_max_liquidity_share: float = 0.02
    risk_block_on_existing_position: bool = True
    paper_require_risk_approval: bool = True
    paper_auto_close_enabled: bool = True
    paper_take_profit_delta: float = 0.08
    paper_stop_loss_delta: float = 0.05
    paper_max_hold_minutes: int = 360
    news_retry_max_attempts: int = 3
    news_retry_base_delay_seconds: float = 2.0
    openai_retry_max_attempts: int = 2
    openai_retry_base_delay_seconds: float = 2.0
    gamma_retry_max_attempts: int = 3
    gamma_retry_base_delay_seconds: float = 1.0
    scheduler_interval_minutes: float = 15.0
    scheduler_news_batch_limit: int = 10
    scheduler_news_fetch_every_n_cycles: int = 1
    scheduler_continue_on_item_error: bool = True
    scheduler_lock_enabled: bool = True
    scheduler_lock_key: int = 48151623
    operator_recent_signals_default_limit: int = 20
    operator_recent_signals_max_limit: int = 100
    alert_mode: str = "noop"
    alert_on_trade_opened: bool = True
    alert_on_trade_closed: bool = True
    alert_on_scheduler_error: bool = True
    alert_on_cycle_summary: bool = False
    alert_on_daily_report: bool = True
    daily_report_window_hours: int = 24
    daily_report_hour_utc: int = 0
    daily_report_minute_utc: int = 10
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    telegram_enabled: bool = True
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
