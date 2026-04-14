from app.services.risk_engine import evaluate_risk_case
from tests.helpers import build_test_settings


def test_risk_engine_allows_clean_actionable_signal() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=45,
        liquidity=200000.0,
        edge=0.08,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
    )

    assert result.allow is True
    assert result.blockers == []
    assert result.approved_size_usd == 50.0


def test_risk_engine_blocks_stale_duplicate_and_daily_limit() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=720,
        liquidity=200000.0,
        edge=0.08,
        existing_open_position=True,
        daily_exposure_used_usd=250.0,
    )

    assert result.allow is False
    assert "news_too_old:720>360" in result.blockers
    assert "duplicate_market_position_exists" in result.blockers
    assert "daily_limit_reached:250.00>=250.00" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_blocks_low_liquidity_and_priced_in_signal() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=10,
        liquidity=1000.0,
        edge=0.02,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
    )

    assert result.allow is False
    assert "liquidity_too_low:1000.00<10000.00" in result.blockers
    assert "priced_in_or_converged:0.0200<=0.0300" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_allows_older_signal_inside_extended_window_with_smaller_size() -> None:
    settings = build_test_settings(
        risk_enable_extended_news_age_window=True,
        risk_extended_max_news_age_minutes=1800,
        risk_extended_news_age_size_multiplier=0.5,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=1476,
        liquidity=200000.0,
        edge=0.08,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
    )

    assert result.allow is True
    assert result.blockers == []
    assert result.approved_size_usd == 25.0


def test_risk_engine_still_blocks_signal_beyond_extended_window() -> None:
    settings = build_test_settings(
        risk_enable_extended_news_age_window=True,
        risk_extended_max_news_age_minutes=1800,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=2000,
        liquidity=200000.0,
        edge=0.08,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
    )

    assert result.allow is False
    assert "news_too_old:2000>1800" in result.blockers
    assert result.approved_size_usd == 0.0
