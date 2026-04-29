import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.enums import MarketSide
from app.services.paper_trader import (
    PaperTrader,
    build_paper_trade_analytics,
    calculate_current_edge,
    evaluate_auto_close_case,
    evaluate_opposite_news_exit_case,
    select_exit_market_price,
)
from tests.helpers import build_test_settings


class FakeReportTradeRepository:
    def __init__(self, positions, trades):
        self.positions = positions
        self.trades = trades

    async def list_open_positions(self):
        return self.positions

    async def get_open_trade_for_position(self, *, position_id: int):
        return self.trades.get(position_id)


class FakeReportMarketClient:
    def __init__(self, markets):
        self.markets = markets

    async def fetch_market(self, market_id: str):
        return self.markets.get(market_id)


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


def test_auto_close_triggers_when_edge_evaporates_after_grace_period() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.20,
        paper_stop_loss_delta=0.20,
        paper_max_hold_minutes=360,
        paper_edge_exit_enabled=True,
        paper_min_current_edge=0.0,
        paper_max_edge_deterioration=0.12,
        paper_edge_exit_grace_minutes=30,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.44,
        current_price=0.58,
        holding_minutes=45,
        entry_edge=0.22,
        current_edge=-0.01,
    )

    assert should_close is True
    assert close_reason == "edge_evaporated:-0.0100<=0.0000"
    assert delta == 0.14


def test_auto_close_triggers_when_edge_deteriorates_after_grace_period() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.20,
        paper_stop_loss_delta=0.20,
        paper_max_hold_minutes=360,
        paper_edge_exit_enabled=True,
        paper_min_current_edge=0.0,
        paper_max_edge_deterioration=0.12,
        paper_edge_exit_grace_minutes=30,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.44,
        current_price=0.50,
        holding_minutes=45,
        entry_edge=0.22,
        current_edge=0.08,
    )

    assert should_close is True
    assert close_reason == "edge_deteriorated:-0.1400<=-0.1200"
    assert delta == 0.06


def test_auto_close_does_not_use_edge_exit_during_grace_period() -> None:
    settings = build_test_settings(
        paper_take_profit_delta=0.20,
        paper_stop_loss_delta=0.20,
        paper_max_hold_minutes=360,
        paper_edge_exit_enabled=True,
        paper_min_current_edge=0.0,
        paper_max_edge_deterioration=0.12,
        paper_edge_exit_grace_minutes=30,
    )

    should_close, close_reason, delta = evaluate_auto_close_case(
        settings=settings,
        entry_price=0.44,
        current_price=0.50,
        holding_minutes=10,
        entry_edge=0.22,
        current_edge=-0.01,
    )

    assert should_close is False
    assert close_reason is None
    assert delta == 0.06


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


def test_calculate_current_edge_uses_side_aligned_probability_and_price() -> None:
    assert calculate_current_edge(fair_probability=0.66, current_price=0.44) == 0.22
    assert calculate_current_edge(fair_probability=None, current_price=0.44) is None


def test_inspect_open_positions_reports_edge_exit_without_closing() -> None:
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    analysis = SimpleNamespace(
        id=101,
        news_item_id=201,
        market_query="bitcoin etf approval",
        news_item=SimpleNamespace(title="ETF approval odds rise", source="coindesk"),
    )
    signal = SimpleNamespace(
        id=11,
        analysis=analysis,
        fair_probability=0.66,
        edge=0.22,
    )
    position = SimpleNamespace(
        id=7,
        signal_id=11,
        signal=signal,
        market_id="btc-etf",
        market_question="Will a Bitcoin ETF be approved?",
        side=MarketSide.YES,
        entry_price=0.44,
        size_usd=50.0,
        shares=113.636363,
        opened_at=opened_at,
    )
    trade = SimpleNamespace(id=17, entry_price=0.44, opened_at=opened_at)
    market = SimpleNamespace(
        yes_price=0.58,
        no_price=0.42,
        last_trade_price=0.58,
        liquidity=10000.0,
        best_bid=0.57,
        best_ask=0.59,
        closed=False,
        active=True,
        end_date=None,
        resolution_source=None,
    )
    trader = PaperTrader(
        settings=build_test_settings(
            paper_take_profit_delta=0.20,
            paper_stop_loss_delta=0.20,
            paper_edge_exit_enabled=True,
            paper_max_edge_deterioration=0.12,
            paper_edge_exit_grace_minutes=30,
            paper_opposite_news_exit_enabled=False,
        ),
        signal_repository=SimpleNamespace(),
        analysis_repository=SimpleNamespace(),
        trade_repository=FakeReportTradeRepository({position.id: position}.values(), {7: trade}),
        forecast_observation_repository=SimpleNamespace(),
        runtime_flag_repository=SimpleNamespace(),
        market_client=FakeReportMarketClient({"btc-etf": market}),
    )

    report = asyncio.run(trader.inspect_open_positions())

    assert report.count == 1
    assert report.would_close_count == 1
    assert report.held_count == 0
    assert report.skipped_count == 0
    item = report.items[0]
    assert item.action == "WOULD_CLOSE"
    assert item.close_reason == "edge_deteriorated:-0.1400<=-0.1200"
    assert item.current_price == 0.58
    assert item.current_edge == 0.08
    assert item.edge_delta == -0.14
    assert item.news_title == "ETF approval odds rise"


def test_opposite_news_exit_triggers_for_same_entity_opposite_direction() -> None:
    settings = build_test_settings(
        paper_opposite_news_exit_enabled=True,
        paper_opposite_news_min_confidence=0.70,
        paper_opposite_news_min_relevance=0.60,
        paper_opposite_news_min_token_overlap=1,
    )

    should_close, reason = evaluate_opposite_news_exit_case(
        settings=settings,
        position_side="YES",
        position_query="trump crypto tax 2027",
        candidate_direction="NO",
        candidate_query="trump crypto tax",
        candidate_confidence=0.84,
        candidate_relevance=0.72,
    )

    assert should_close is True
    assert reason == (
        "opposite_news_thesis_break:YES->NO,"
        "confidence=0.8400,relevance=0.7200,overlap=3"
    )


def test_opposite_news_exit_ignores_unrelated_opposite_direction() -> None:
    settings = build_test_settings(
        paper_opposite_news_exit_enabled=True,
        paper_opposite_news_min_confidence=0.70,
        paper_opposite_news_min_relevance=0.60,
        paper_opposite_news_min_token_overlap=1,
    )

    should_close, reason = evaluate_opposite_news_exit_case(
        settings=settings,
        position_side="YES",
        position_query="trump crypto tax 2027",
        candidate_direction="NO",
        candidate_query="fed rate cuts",
        candidate_confidence=0.90,
        candidate_relevance=0.90,
    )

    assert should_close is False
    assert reason is None


def test_opposite_news_exit_requires_confidence_gate() -> None:
    settings = build_test_settings(
        paper_opposite_news_exit_enabled=True,
        paper_opposite_news_min_confidence=0.70,
        paper_opposite_news_min_relevance=0.60,
        paper_opposite_news_min_token_overlap=1,
    )

    should_close, reason = evaluate_opposite_news_exit_case(
        settings=settings,
        position_side="YES",
        position_query="trump crypto tax 2027",
        candidate_direction="NO",
        candidate_query="trump crypto tax",
        candidate_confidence=0.50,
        candidate_relevance=0.90,
    )

    assert should_close is False
    assert reason is None


def test_select_exit_market_price_uses_no_price_for_no_side() -> None:
    current_price = select_exit_market_price(
        side="NO",
        yes_price=0.63,
        no_price=0.37,
        last_trade_price=0.62,
    )

    assert current_price == 0.37


def test_select_exit_market_price_uses_inverse_last_trade_for_no_side() -> None:
    current_price = select_exit_market_price(
        side="NO",
        yes_price=None,
        no_price=None,
        last_trade_price=0.63,
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
