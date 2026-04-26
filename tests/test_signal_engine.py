import pytest

from app.services.signal_engine import evaluate_signal_candidate
from tests.helpers import build_test_settings


def test_signal_engine_returns_actionable_for_strong_signal() -> None:
    settings = build_test_settings()

    signal_status, edge = evaluate_signal_candidate(
        settings=settings,
        direction="YES",
        relevance=0.80,
        confidence=0.81,
        fair_probability=0.70,
        market_price=0.60,
    )

    assert signal_status == "ACTIONABLE"
    assert edge == pytest.approx(0.10)


def test_signal_engine_returns_watchlist_for_positive_but_weak_edge() -> None:
    settings = build_test_settings()

    signal_status, edge = evaluate_signal_candidate(
        settings=settings,
        direction="YES",
        relevance=0.60,
        confidence=0.69,
        fair_probability=0.58,
        market_price=0.52,
    )

    assert signal_status == "WATCHLIST"
    assert edge == pytest.approx(0.06)


def test_signal_engine_rejects_none_direction() -> None:
    settings = build_test_settings()

    signal_status, edge = evaluate_signal_candidate(
        settings=settings,
        direction="NONE",
        relevance=0.95,
        confidence=0.95,
        fair_probability=0.50,
        market_price=0.48,
    )

    assert signal_status == "REJECTED"
    assert edge == pytest.approx(0.02)


def test_signal_engine_rejects_weak_market_match_even_with_large_edge() -> None:
    settings = build_test_settings(risk_min_match_score=0.35)

    signal_status, edge = evaluate_signal_candidate(
        settings=settings,
        direction="YES",
        relevance=0.95,
        confidence=0.95,
        fair_probability=0.70,
        market_price=0.10,
        match_score=0.19,
    )

    assert signal_status == "REJECTED"
    assert edge == pytest.approx(0.60)
