import pytest

from app.schemas.market import MarketCandidate
from app.services.forecasting import (
    CalibrationPoint,
    build_execution_edge,
    calibrate_probability,
    resolve_market_resolution,
)
from tests.helpers import build_test_settings


def test_calibrate_probability_uses_resolved_bucket_history() -> None:
    settings = build_test_settings(
        signal_calibration_enabled=True,
        signal_calibration_min_samples=3,
        signal_calibration_bucket_size=0.1,
        signal_calibration_prior_strength=3.0,
    )

    result = calibrate_probability(
        settings=settings,
        raw_probability=0.70,
        history=[
            CalibrationPoint(raw_probability=0.68, outcome_value=1.0),
            CalibrationPoint(raw_probability=0.71, outcome_value=0.0),
            CalibrationPoint(raw_probability=0.74, outcome_value=0.0),
        ],
    )

    assert result.sample_count == 3
    assert result.empirical_rate == pytest.approx(0.3333)
    assert result.calibrated_probability == pytest.approx(0.5167)


def test_build_execution_edge_accounts_for_spread_fees_and_market_consensus() -> None:
    settings = build_test_settings(
        risk_max_trade_size_usd=50.0,
        signal_market_consensus_liquidity_cap=250000.0,
        signal_market_consensus_max_weight=0.35,
        signal_liquidity_penalty_factor=0.20,
        signal_liquidity_penalty_cap=0.03,
    )
    candidate = MarketCandidate(
        analysis_id=1,
        news_item_id=1,
        market_id="btc-100k",
        question="Will Bitcoin reach 100k?",
        yes_price=0.60,
        no_price=0.40,
        best_bid=0.59,
        best_ask=0.62,
        last_trade_price=0.60,
        liquidity=50000.0,
        volume=100000.0,
        fees_enabled=True,
        effective_taker_fee_rate=0.04,
        match_score=0.9,
        correlation_key="btc-100k",
        raw_market={},
    )

    estimate = build_execution_edge(
        settings=settings,
        direction="YES",
        candidate=candidate,
        reference_market_price=0.60,
        raw_probability=0.70,
        calibrated_probability=0.68,
    )

    assert estimate.execution_price == pytest.approx(0.62)
    assert estimate.estimated_fee_per_share == pytest.approx(0.009424)
    assert estimate.market_consensus_weight == pytest.approx(0.07)
    assert estimate.raw_edge == pytest.approx(0.10)
    assert estimate.net_edge == pytest.approx(0.0448)


def test_resolve_market_resolution_detects_yes_winner() -> None:
    market = type(
        "ResolvedMarket",
        (),
        {
            "yes_price": 1.0,
            "no_price": 0.0,
            "closed": True,
            "active": False,
            "end_date": None,
            "resolution_source": "oracle",
        },
    )()

    resolution = resolve_market_resolution(market)

    assert resolution is not None
    assert resolution.outcome_label == "YES"
    assert resolution.yes_outcome_value == pytest.approx(1.0)
