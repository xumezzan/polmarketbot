import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.enums import SignalStatus, VerdictDirection
from app.services.paper_trader import PaperTrader
from tests.helpers import build_test_settings


class FakeSignalRepository:
    def __init__(self, signals):
        self.signals = signals
        self.signal_statuses = None

    async def list_without_observation(self, *, signal_statuses=None):
        self.signal_statuses = signal_statuses
        return self.signals


class FakeForecastObservationRepository:
    def __init__(self):
        self.calls = []

    async def upsert_for_signal(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=len(self.calls), **kwargs)


class FakeMarketClient:
    def __init__(self, markets):
        self.markets = markets

    async def fetch_market(self, market_id: str):
        return self.markets.get(market_id)


class EmptyTradeRepository:
    async def list_open_positions(self):
        return []


def test_sync_resolved_signal_observations_records_actionable_signals() -> None:
    signal_repository = FakeSignalRepository(
        [
            _build_signal(
                signal_id=1,
                analysis_id=101,
                market_id="resolved-yes",
                direction=VerdictDirection.YES,
                raw_probability=0.70,
                calibrated_probability=0.66,
                market_price=0.60,
                execution_price=0.62,
            ),
            _build_signal(
                signal_id=2,
                analysis_id=102,
                market_id="resolved-no",
                direction=VerdictDirection.NO,
                raw_probability=0.64,
                calibrated_probability=0.60,
                market_price=0.41,
                execution_price=None,
                provider=None,
                model=None,
                raw_response={"provider": "gemini", "model": "gemini-2.5-pro"},
            ),
            _build_signal(
                signal_id=3,
                analysis_id=103,
                market_id="unresolved",
                direction=VerdictDirection.YES,
                raw_probability=0.68,
                calibrated_probability=0.63,
                market_price=0.57,
                execution_price=0.58,
            ),
            _build_signal(
                signal_id=4,
                analysis_id=104,
                market_id="skipped-none",
                direction=VerdictDirection.NONE,
                raw_probability=0.50,
                calibrated_probability=0.50,
                market_price=0.50,
                execution_price=0.50,
            ),
        ]
    )
    observation_repository = FakeForecastObservationRepository()
    trader = PaperTrader(
        settings=build_test_settings(),
        signal_repository=signal_repository,
        analysis_repository=SimpleNamespace(),
        trade_repository=EmptyTradeRepository(),
        forecast_observation_repository=observation_repository,
        runtime_flag_repository=SimpleNamespace(),
        market_client=FakeMarketClient(
            {
                "resolved-yes": _build_resolved_market(),
                "resolved-no": _build_resolved_market(),
                "unresolved": _build_unresolved_market(),
                "skipped-none": _build_resolved_market(),
            }
        ),
    )

    result = asyncio.run(
        trader.sync_resolved_signal_observations(
            signal_statuses=[SignalStatus.ACTIONABLE],
        )
    )

    assert signal_repository.signal_statuses == [SignalStatus.ACTIONABLE]
    assert result.evaluated_signals == 4
    assert result.synced_observations == 2
    assert result.unresolved_signals == 1
    assert result.skipped_signals == 1
    assert result.synced_signal_ids == [1, 2]

    assert len(observation_repository.calls) == 2
    first_call, second_call = observation_repository.calls
    assert first_call["signal_id"] == 1
    assert first_call["side"] == "YES"
    assert first_call["outcome_value"] == pytest.approx(1.0)
    assert first_call["brier_score"] == pytest.approx(0.1156)
    assert first_call["position_id"] is None

    assert second_call["signal_id"] == 2
    assert second_call["side"] == "NO"
    assert second_call["provider"] == "gemini"
    assert second_call["model"] == "gemini-2.5-pro"
    assert second_call["execution_price"] == pytest.approx(0.41)
    assert second_call["outcome_value"] == pytest.approx(0.0)
    assert second_call["brier_score"] == pytest.approx(0.36)


def test_paper_trade_maintenance_runs_signal_observation_sync_without_positions() -> None:
    trader = PaperTrader(
        settings=build_test_settings(),
        signal_repository=FakeSignalRepository(
            [
                _build_signal(
                    signal_id=10,
                    analysis_id=110,
                    market_id="resolved-yes",
                    direction=VerdictDirection.YES,
                    raw_probability=0.72,
                    calibrated_probability=0.69,
                    market_price=0.61,
                    execution_price=0.63,
                )
            ]
        ),
        analysis_repository=SimpleNamespace(),
        trade_repository=EmptyTradeRepository(),
        forecast_observation_repository=FakeForecastObservationRepository(),
        runtime_flag_repository=SimpleNamespace(),
        market_client=FakeMarketClient({"resolved-yes": _build_resolved_market()}),
    )

    result = asyncio.run(trader.maintain_open_positions())

    assert result.evaluated_positions == 0
    assert result.closed_positions == 0
    assert result.skipped_positions == 0
    assert result.observation_sync is not None
    assert result.observation_sync.synced_observations == 1
    assert result.observation_sync.synced_signal_ids == [10]


def _build_signal(
    *,
    signal_id: int,
    analysis_id: int,
    market_id: str,
    direction: VerdictDirection,
    raw_probability: float,
    calibrated_probability: float,
    market_price: float,
    execution_price: float | None,
    provider: str | None = "openai",
    model: str | None = "gpt-5.4-mini",
    raw_response: dict | None = None,
):
    analysis = SimpleNamespace(
        id=analysis_id,
        news_item_id=analysis_id + 1000,
        direction=direction,
        llm_provider=provider,
        llm_model=model,
        raw_response=raw_response or {},
    )
    return SimpleNamespace(
        id=signal_id,
        analysis_id=analysis_id,
        market_id=market_id,
        signal_status=SignalStatus.ACTIONABLE,
        raw_fair_probability=raw_probability,
        fair_probability=calibrated_probability,
        market_price=market_price,
        execution_price=execution_price,
        analysis=analysis,
    )


def _build_resolved_market():
    return SimpleNamespace(
        yes_price=1.0,
        no_price=0.0,
        closed=True,
        active=False,
        end_date=datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
        resolution_source="oracle",
    )


def _build_unresolved_market():
    return SimpleNamespace(
        yes_price=0.58,
        no_price=0.42,
        closed=False,
        active=True,
        end_date=None,
        resolution_source="oracle",
    )
