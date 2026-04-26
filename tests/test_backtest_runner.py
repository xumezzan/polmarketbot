import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.enums import SignalStatus, VerdictDirection
from app.schemas.historical_prices import BatchPriceHistoryResult, PriceHistoryPoint
from app.services.backtest_runner import BacktestRunner
from tests.helpers import build_test_settings


class FakeSignalRepository:
    def __init__(self, signals):
        self.signals = signals

    async def list_created_between(self, *, since, until, signal_statuses=None):
        filtered = [
            signal
            for signal in self.signals
            if since <= signal.created_at <= until
        ]
        if signal_statuses:
            allowed = {status.value for status in signal_statuses}
            filtered = [
                signal
                for signal in filtered
                if signal.signal_status.value in allowed
            ]
        return filtered


class FakeHistoricalPriceClient:
    def __init__(self, history):
        self.history = history

    async def fetch_batch_prices_history(
        self,
        *,
        market_ids,
        start_ts=None,
        end_ts=None,
        interval="1h",
        fidelity=1,
    ):
        return BatchPriceHistoryResult(
            history={
                market_id: self.history.get(market_id, [])
                for market_id in market_ids
            }
        )


class FakeMarketClient:
    def __init__(self, markets):
        self.markets = markets

    async def fetch_market(self, market_id: str):
        return self.markets.get(market_id)


def test_backtest_runner_scores_yes_and_no_signals() -> None:
    created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    signal_yes = _build_signal(
        signal_id=1,
        analysis_id=101,
        market_id="mkt-yes",
        created_at=created_at,
        direction=VerdictDirection.YES,
        raw_probability=0.70,
        calibrated_probability=0.66,
        edge=0.04,
        token_id="yes-token-1",
    )
    signal_no = _build_signal(
        signal_id=2,
        analysis_id=102,
        market_id="mkt-no",
        created_at=created_at,
        direction=VerdictDirection.NO,
        raw_probability=0.64,
        calibrated_probability=0.60,
        edge=0.03,
        token_id="no-token-2",
    )
    runner = BacktestRunner(
        settings=build_test_settings(signal_calibration_bucket_size=0.1),
        signal_repository=FakeSignalRepository([signal_yes, signal_no]),
        market_client=FakeMarketClient(
            {
                "mkt-yes": SimpleNamespace(
                    yes_price=1.0,
                    no_price=0.0,
                    closed=True,
                    active=False,
                    end_date=datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
                    resolution_source="oracle",
                ),
                "mkt-no": SimpleNamespace(
                    yes_price=1.0,
                    no_price=0.0,
                    closed=True,
                    active=False,
                    end_date=datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
                    resolution_source="oracle",
                ),
            }
        ),
        historical_price_client=FakeHistoricalPriceClient(
            {
                "yes-token-1": [
                    PriceHistoryPoint(timestamp=int(datetime(2026, 4, 1, 12, 5, tzinfo=UTC).timestamp()), price=0.62)
                ],
                "no-token-2": [
                    PriceHistoryPoint(timestamp=int(datetime(2026, 4, 1, 12, 5, tzinfo=UTC).timestamp()), price=0.55)
                ],
            }
        ),
    )

    result = asyncio.run(
        runner.run_signal_replay(
            since=datetime(2026, 4, 1, 0, 0, tzinfo=UTC),
            until=datetime(2026, 4, 1, 23, 59, tzinfo=UTC),
            signal_statuses=[SignalStatus.ACTIONABLE],
            entry_lag_minutes=5,
            interval="1h",
        )
    )

    assert result.summary.signals_total == 2
    assert result.summary.signals_scored == 2
    assert result.summary.win_rate == pytest.approx(0.5)
    assert result.summary.avg_predicted_net_edge == pytest.approx(0.035)
    assert result.summary.avg_realized_edge == pytest.approx(-0.085)
    assert result.summary.avg_raw_brier == pytest.approx(0.2498)
    assert result.summary.avg_calibrated_brier == pytest.approx(0.2378)
    assert len(result.buckets) == 2
    assert [bucket.bucket for bucket in result.buckets] == ["0.55-0.65", "0.65-0.75"]
    assert result.rows[0].entry_price_historical == pytest.approx(0.62)
    assert result.rows[0].outcome_value == pytest.approx(1.0)
    assert result.rows[1].direction == "NO"
    assert result.rows[1].outcome_value == pytest.approx(0.0)


def test_backtest_runner_tracks_skip_reasons() -> None:
    created_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    signal_none = _build_signal(
        signal_id=10,
        analysis_id=110,
        market_id="mkt-none",
        created_at=created_at,
        direction=VerdictDirection.NONE,
        raw_probability=0.50,
        calibrated_probability=0.50,
        edge=0.0,
        token_id="yes-token-none",
    )
    signal_missing_token = _build_signal(
        signal_id=11,
        analysis_id=111,
        market_id="mkt-missing-token",
        created_at=created_at,
        direction=VerdictDirection.YES,
        raw_probability=0.70,
        calibrated_probability=0.68,
        edge=0.02,
        token_id=None,
    )
    signal_missing_history = _build_signal(
        signal_id=12,
        analysis_id=112,
        market_id="mkt-missing-history",
        created_at=created_at,
        direction=VerdictDirection.YES,
        raw_probability=0.72,
        calibrated_probability=0.69,
        edge=0.03,
        token_id="yes-token-missing-history",
    )
    signal_unresolved = _build_signal(
        signal_id=13,
        analysis_id=113,
        market_id="mkt-unresolved",
        created_at=created_at,
        direction=VerdictDirection.YES,
        raw_probability=0.65,
        calibrated_probability=0.61,
        edge=0.01,
        token_id="yes-token-unresolved",
    )

    runner = BacktestRunner(
        settings=build_test_settings(),
        signal_repository=FakeSignalRepository(
            [signal_none, signal_missing_token, signal_missing_history, signal_unresolved]
        ),
        market_client=FakeMarketClient(
            {
                "mkt-unresolved": SimpleNamespace(
                    yes_price=0.58,
                    no_price=0.42,
                    closed=False,
                    active=True,
                    end_date=None,
                    resolution_source="oracle",
                )
            }
        ),
        historical_price_client=FakeHistoricalPriceClient(
            {
                "yes-token-unresolved": [
                    PriceHistoryPoint(timestamp=int(datetime(2026, 4, 1, 12, 5, tzinfo=UTC).timestamp()), price=0.57)
                ]
            }
        ),
    )

    result = asyncio.run(
        runner.run_signal_replay(
            since=datetime(2026, 4, 1, 0, 0, tzinfo=UTC),
            until=datetime(2026, 4, 1, 23, 59, tzinfo=UTC),
            signal_statuses=None,
            include_unresolved=True,
        )
    )

    assert result.summary.signals_total == 4
    assert result.summary.direction_none_skipped_count == 1
    assert result.summary.missing_token_count == 1
    assert result.summary.missing_history_count == 1
    assert result.summary.unresolved_count == 1
    assert result.summary.signals_scored == 0
    assert [row.skip_reason for row in result.rows] == [
        "direction_none",
        "entry_token_id_missing",
        "historical_entry_price_missing",
        "market_unresolved",
    ]


def _build_signal(
    *,
    signal_id: int,
    analysis_id: int,
    market_id: str,
    created_at: datetime,
    direction: VerdictDirection,
    raw_probability: float,
    calibrated_probability: float,
    edge: float,
    token_id: str | None,
):
    candidate = {
        "analysis_id": analysis_id,
        "news_item_id": analysis_id + 1000,
        "market_id": market_id,
        "question": f"Question for {market_id}",
        "yes_token_id": token_id if direction == VerdictDirection.YES else "yes-fallback",
        "no_token_id": token_id if direction == VerdictDirection.NO else None,
        "yes_price": 0.60,
        "no_price": 0.40,
        "best_bid": 0.59,
        "best_ask": 0.61,
        "last_trade_price": 0.60,
        "liquidity": 100000.0,
        "volume": 500000.0,
        "match_score": 0.9,
        "correlation_key": market_id,
        "raw_market": {},
    }
    analysis = SimpleNamespace(
        id=analysis_id,
        news_item_id=analysis_id + 1000,
        direction=direction,
        raw_response={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "snapshots": {
                "signal_engine": {
                    "signals": [
                        {
                            "signal_id": signal_id,
                            "market_id": market_id,
                            "candidate": candidate,
                        }
                    ]
                }
            },
        },
    )
    return SimpleNamespace(
        id=signal_id,
        analysis_id=analysis_id,
        market_id=market_id,
        created_at=created_at,
        signal_status=SignalStatus.ACTIONABLE,
        edge=edge,
        fair_probability=calibrated_probability,
        raw_fair_probability=raw_probability,
        analysis=analysis,
    )
