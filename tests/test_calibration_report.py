import pytest

from app.services.calibration_report import (
    build_forecast_calibration_report,
    calculate_log_loss,
)


def test_calculate_log_loss_clamps_extreme_probabilities() -> None:
    assert calculate_log_loss(probability=1.0, outcome_value=1.0) == pytest.approx(0.000001)
    assert calculate_log_loss(probability=0.0, outcome_value=1.0) == pytest.approx(13.815511)


def test_build_forecast_calibration_report_returns_reliability_buckets() -> None:
    report = build_forecast_calibration_report(
        generated_at="2026-04-29T00:00:00+00:00",
        window_days=30,
        bucket_size=0.10,
        rows=[
            {
                "raw_probability": 0.62,
                "calibrated_probability": 0.60,
                "outcome_value": 1.0,
                "source": "coindesk",
                "model": "gpt-5.4-mini",
                "topic": "bitcoin etf",
            },
            {
                "raw_probability": 0.68,
                "calibrated_probability": 0.65,
                "outcome_value": 0.0,
                "source": "coindesk",
                "model": "gpt-5.4-mini",
                "topic": "bitcoin etf",
            },
            {
                "raw_probability": 0.82,
                "calibrated_probability": 0.80,
                "outcome_value": 1.0,
                "source": "wsj",
                "model": "gpt-5.4-mini",
                "topic": "fed chair",
            },
        ],
    )

    assert report.resolved_observations == 3
    assert report.avg_raw_brier == pytest.approx(0.213067)
    assert report.avg_calibrated_brier == pytest.approx(0.2075)
    assert report.buckets[0].bucket == "0.60-0.70"
    assert report.buckets[0].count == 2
    assert report.buckets[0].actual_frequency == pytest.approx(0.5)
    assert report.buckets[0].calibration_error == pytest.approx(0.125)
    assert report.by_source[0].key == "coindesk"
    assert report.by_source[0].count == 2
    assert report.by_topic[0].key == "bitcoin etf"
    assert "need_more_resolved_observations:3<30" in report.notes
