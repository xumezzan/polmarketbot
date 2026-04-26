from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.scheduler import build_skipped_ingestion_result, should_run_news_ingestion
from app.scheduler import select_pending_news_for_cycle, should_skip_market_pipeline_for_direction
from tests.helpers import build_test_settings


def test_scheduler_runs_news_ingestion_every_cycle_by_default() -> None:
    assert should_run_news_ingestion(cycle_number=1, every_n_cycles=1) is True
    assert should_run_news_ingestion(cycle_number=2, every_n_cycles=1) is True


def test_scheduler_runs_news_ingestion_every_six_cycles() -> None:
    results = [
        should_run_news_ingestion(cycle_number=cycle_number, every_n_cycles=6)
        for cycle_number in range(1, 13)
    ]

    assert results == [
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_build_skipped_ingestion_result_returns_zeroed_payload() -> None:
    result = build_skipped_ingestion_result(source_mode="newsapi")

    assert result.source_mode == "newsapi"
    assert result.fetched_count == 0
    assert result.normalized_count == 0
    assert result.inserted_count == 0
    assert result.skipped_count == 0
    assert result.filtered_out_count == 0


def test_select_pending_news_for_cycle_prefers_newest_fresh_items() -> None:
    now = datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    settings = build_test_settings(
        scheduler_news_batch_limit=2,
        risk_max_news_age_minutes=360,
    )
    items = [
        SimpleNamespace(id=1, published_at=now - timedelta(minutes=720)),
        SimpleNamespace(id=2, published_at=now - timedelta(minutes=15)),
        SimpleNamespace(id=3, published_at=now - timedelta(minutes=45)),
    ]

    selected, stale = select_pending_news_for_cycle(
        items=items,
        settings=settings,
        now=now,
    )

    assert [item.id for item in selected] == [2, 3]
    assert [item.id for item in stale] == [1]


def test_select_pending_news_for_cycle_keeps_unknown_timestamps() -> None:
    now = datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    settings = build_test_settings(
        scheduler_news_batch_limit=2,
        risk_max_news_age_minutes=360,
    )
    items = [
        SimpleNamespace(id=1, published_at=None),
        SimpleNamespace(id=2, published_at=now - timedelta(minutes=10)),
    ]

    selected, stale = select_pending_news_for_cycle(
        items=items,
        settings=settings,
        now=now,
    )

    assert [item.id for item in selected] == [1, 2]
    assert stale == []


def test_scheduler_skips_market_pipeline_for_neutral_verdicts() -> None:
    assert should_skip_market_pipeline_for_direction("NONE") is True
    assert should_skip_market_pipeline_for_direction("YES") is False
    assert should_skip_market_pipeline_for_direction("NO") is False
