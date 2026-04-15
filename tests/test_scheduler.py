from app.scheduler import build_skipped_ingestion_result, should_run_news_ingestion


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
