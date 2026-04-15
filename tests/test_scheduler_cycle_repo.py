from datetime import UTC, datetime
from types import SimpleNamespace

from app.repositories.scheduler_cycle_repo import (
    classify_cycle_error_provider,
    infer_newsapi_rate_limit_cooldown,
    is_idle_scheduler_cycle,
    is_rate_limited_cycle_error,
)


def test_classify_cycle_error_provider_detects_newsapi() -> None:
    provider = classify_cycle_error_provider(
        "NewsAPI request failed: Client error '429 Too Many Requests'"
    )

    assert provider == "newsapi"


def test_classify_cycle_error_provider_detects_openai() -> None:
    provider = classify_cycle_error_provider("OpenAI analysis failed: timeout")

    assert provider == "openai"


def test_is_idle_scheduler_cycle_true_only_for_completed_zero_work_cycle() -> None:
    cycle = SimpleNamespace(
        status="COMPLETED",
        inserted_news_count=0,
        processed_news_count=0,
        actionable_signal_count=0,
        opened_position_count=0,
    )

    assert is_idle_scheduler_cycle(cycle) is True


def test_is_idle_scheduler_cycle_false_for_failed_cycle() -> None:
    cycle = SimpleNamespace(
        status="FAILED",
        inserted_news_count=0,
        processed_news_count=0,
        actionable_signal_count=0,
        opened_position_count=0,
    )

    assert is_idle_scheduler_cycle(cycle) is False


def test_is_rate_limited_cycle_error_detects_429() -> None:
    assert is_rate_limited_cycle_error("Client error '429 Too Many Requests'") is True


def test_infer_newsapi_rate_limit_cooldown_returns_active_window() -> None:
    now = datetime(2026, 4, 15, 12, 35, tzinfo=UTC)
    cycles = [
        SimpleNamespace(
            error="NewsAPI request failed: Client error '429 Too Many Requests'",
            finished_at=datetime(2026, 4, 15, 12, 33, tzinfo=UTC),
            started_at=datetime(2026, 4, 15, 12, 33, tzinfo=UTC),
        )
    ]

    cooldown = infer_newsapi_rate_limit_cooldown(
        cycles=cycles,
        now=now,
        cooldown_minutes=30,
    )

    assert cooldown is not None
    cooldown_until, remaining_seconds = cooldown
    assert cooldown_until == datetime(2026, 4, 15, 13, 3, tzinfo=UTC)
    assert remaining_seconds == 1680


def test_infer_newsapi_rate_limit_cooldown_returns_none_when_expired() -> None:
    now = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
    cycles = [
        SimpleNamespace(
            error="NewsAPI request failed: Client error '429 Too Many Requests'",
            finished_at=datetime(2026, 4, 15, 12, 33, tzinfo=UTC),
            started_at=datetime(2026, 4, 15, 12, 33, tzinfo=UTC),
        )
    ]

    cooldown = infer_newsapi_rate_limit_cooldown(
        cycles=cycles,
        now=now,
        cooldown_minutes=30,
    )

    assert cooldown is None
