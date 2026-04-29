from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.schemas.scheduler import PipelineItemResult, SchedulerCycleResult
from app.services.anomaly_hunter import build_anomaly_hypotheses, build_cycle_observations


def test_build_cycle_observations_captures_dead_zone_and_risk_bottleneck() -> None:
    observed_at = datetime(2026, 4, 29, 10, 0, tzinfo=UTC)
    result = SchedulerCycleResult(
        cycle_id="cycle-1",
        started_at=observed_at.isoformat(),
        finished_at=observed_at.isoformat(),
        source_mode="rss",
        llm_mode="openai",
        fetch_mode="gamma",
        inserted_news_count=2,
        pending_news_count=2,
        processed_news_count=2,
        actionable_signal_count=1,
        approved_signal_count=0,
        opened_position_count=0,
        auto_close_evaluated_count=0,
        closed_position_count=0,
        error_count=1,
        item_results=[
            PipelineItemResult(
                news_item_id=10,
                analysis_id=20,
                market_candidate_count=0,
                actionable_signal_count=1,
                approved_signal_count=0,
                blocked_signal_count=1,
            )
        ],
        closed_trade_ids=[],
    )

    observations = build_cycle_observations(result=result, observed_at=observed_at)
    observation_types = {item.observation_type for item in observations}

    assert "cycle_summary" in observation_types
    assert "cycle_error" in observation_types
    assert "market_matching_dead_zone" in observation_types
    assert "risk_bottleneck" in observation_types


def test_build_anomaly_hypotheses_detects_dead_zone_and_instability() -> None:
    generated_at = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
    window_start = generated_at - timedelta(hours=6)
    observations = [
        SimpleNamespace(observation_type="market_matching_dead_zone"),
        SimpleNamespace(observation_type="market_matching_dead_zone"),
        SimpleNamespace(observation_type="market_matching_dead_zone"),
        SimpleNamespace(observation_type="risk_bottleneck"),
        SimpleNamespace(observation_type="risk_bottleneck"),
    ]
    cycles = [
        SimpleNamespace(status="COMPLETED", error_count=1, error=None),
        SimpleNamespace(status="FAILED", error_count=1, error="Gamma market fetch failed"),
    ]

    hypotheses = build_anomaly_hypotheses(
        generated_at=generated_at,
        window_start=window_start,
        window_end=generated_at,
        observations=observations,
        cycles=cycles,
    )
    hypothesis_types = {item.hypothesis_type for item in hypotheses}

    assert "pipeline_instability" in hypothesis_types
    assert "market_matching_dead_zone" in hypothesis_types
    assert "risk_bottleneck" in hypothesis_types
    assert hypotheses[0].score >= hypotheses[-1].score
