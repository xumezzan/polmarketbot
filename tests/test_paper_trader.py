from collections import Counter

from app.services.paper_trader import (
    build_paper_trade_analytics,
    evaluate_auto_close_case,
    select_exit_market_price,
)
from tests.helpers import build_test_settings


def test_auto_close_triggers_take_profit() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.64,
        holding_minutes=30,
    )

    assert should_close is True
    assert close_reason == "take_profit_reached:0.0900>=0.0800"
    assert delta == 0.09


def test_auto_close_triggers_stop_loss() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.49,
        holding_minutes=20,
    )

    assert should_close is True
    assert close_reason == "stop_loss_reached:-0.0600<=-0.0500"
    assert delta == -0.06


def test_auto_close_triggers_max_hold_time() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=180,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.57,
        holding_minutes=181,
    )

    assert should_close is True
    assert close_reason == "max_holding_time_reached:181.00>=180"
    assert delta == 0.02


def test_auto_close_holds_when_exit_rules_not_met() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.08,
        paper_stop_loss_delta=0.05,
        paper_max_hold_minutes=360,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.55,
        current_price=0.58,
        holding_minutes=45,
    )

    assert should_close is False
    assert close_reason is None
    assert delta == 0.03


def test_select_exit_market_price_uses_no_price_for_no_side() -> None:
    current_price = select_exit_market_price(
        side="NO",
        yes_price=0.63,
        no_price=0.37,
        last_trade_price=0.62,
    )

    assert current_price == 0.37


def test_build_paper_trade_analytics_returns_daily_market_and_source_breakdowns() -> None:
    analytics = build_paper_trade_analytics(
        generated_at="2026-04-13T12:00:00+00:00",
        period_days=7,
        current_open_positions=1,
        analyses_count=4,
        actionable_signal_count=3,
        approved_signal_count=2,
        blocked_signal_count=1,
        blocker_counts=Counter({"priced_in_or_converged": 2, "news_too_old": 1}),
        trade_rows=[
            {
                "opened_in_period": True,
                "closed_in_period": True,
                "opened_date": "2026-04-12",
                "closed_date": "2026-04-13",
                "market_id": "btc-1",
                "market_question": "Will BTC hit 100k?",
                "news_source": "cointelegraph",
                "pnl": 5.0,
                "holding_minutes": 120.0,
            },
            {
                "opened_in_period": True,
                "closed_in_period": True,
                "opened_date": "2026-04-13",
                "closed_date": "2026-04-13",
                "market_id": "btc-1",
                "market_question": "Will BTC hit 100k?",
                "news_source": "cointelegraph",
                "pnl": -2.0,
                "holding_minutes": 60.0,
            },
            {
                "opened_in_period": True,
                "closed_in_period": False,
                "opened_date": "2026-04-13",
                "closed_date": None,
                "market_id": "eth-1",
                "market_question": "Will ETH outperform BTC?",
                "news_source": "the block",
                "pnl": 0.0,
                "holding_minutes": 0.0,
            },
        ],
    )

    assert analytics.summary.opened_trades == 3
    assert analytics.summary.closed_trades == 2
    assert analytics.summary.current_open_positions == 1
    assert analytics.summary.total_pnl == 3.0
    assert analytics.summary.avg_holding_minutes == 90.0
    assert analytics.funnel.analysis_to_actionable_rate == 0.75
    assert analytics.funnel.actionable_to_approved_rate == 0.6667
    assert analytics.funnel.approved_to_opened_rate == 1.5
    assert analytics.daily[0].date == "2026-04-12"
    assert analytics.daily[1].date == "2026-04-13"
    assert analytics.by_market[0].key == "btc-1"
    assert analytics.by_market[0].total_pnl == 3.0
    assert analytics.by_source[0].key == "cointelegraph"
    assert analytics.risk_blockers[0].blocker == "priced_in_or_converged"
    assert analytics.risk_blockers[0].count == 2
