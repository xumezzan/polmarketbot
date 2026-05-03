from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.live_execution as live_execution_module
from app.models.enums import ExecutionIntentStatus, LiveOrderStatus, MarketSide, VerdictDirection
from app.schemas.market import GammaMarket
from app.services.live_execution import (
    CircuitBreakerTriggeredError,
    LiveEdgeGateBlockedError,
    LiveExecutionService,
    LiveTradingDisabledError,
    StubCLOBClient,
)
from tests.helpers import build_test_settings


class FakeSignalRepository:
    def __init__(self, signal) -> None:
        self.signal = signal

    async def get_by_id(self, signal_id: int):
        if self.signal.id == signal_id:
            return self.signal
        return None


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.actions: list[dict[str, object]] = []

    async def save_execution_action(self, *, analysis_id: int, action: dict[str, object]):
        self.actions.append({"analysis_id": analysis_id, **action})
        return None


class FakeRuntimeFlagRepository:
    def __init__(self, flags: dict[str, bool] | None = None) -> None:
        self.flags = flags or {}

    async def get_bool(self, *, key: str, default: bool = False) -> bool:
        return self.flags.get(key, default)

    async def set_bool(self, *, key: str, value: bool):
        self.flags[key] = value
        return SimpleNamespace(key=key, bool_value=value, updated_at=datetime.now(UTC))


class FakeExecutionIntentRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.intents = []

    async def create(self, **kwargs):
        intent = SimpleNamespace(
            id=self._next_id,
            created_at=datetime.now(UTC),
            executed_at=kwargs.get("executed_at"),
            **kwargs,
        )
        self._next_id += 1
        self.intents.append(intent)
        return intent

    async def mark_submitted(self, *, intent, exchange_order_id, simulation_result):
        intent.status = ExecutionIntentStatus.SUBMITTED
        intent.exchange_order_id = exchange_order_id
        intent.simulation_result = simulation_result
        intent.executed_at = datetime.now(UTC)
        return intent

    async def mark_failed(self, *, intent, error, simulation_result=None):
        intent.status = ExecutionIntentStatus.FAILED
        intent.error = error
        intent.simulation_result = simulation_result
        intent.executed_at = datetime.now(UTC)
        return intent


class FakeLiveTradeRepository:
    def __init__(self, *, open_positions: int = 0, daily_exposure: float = 0.0) -> None:
        self.open_positions = open_positions
        self.daily_exposure = daily_exposure
        self.orders = []
        self.positions = []

    async def count_open_positions(self) -> int:
        return self.open_positions

    async def sum_daily_exposure_used_usd(self, *, day_start: datetime) -> float:
        return self.daily_exposure

    async def create_order(self, **kwargs):
        order = SimpleNamespace(id=len(self.orders) + 1, **kwargs)
        self.orders.append(order)
        return order

    async def create_position(self, **kwargs):
        position = SimpleNamespace(id=len(self.positions) + 1, **kwargs)
        self.positions.append(position)
        self.open_positions += 1
        return position

    async def list_open_positions(self):
        return self.positions


class FakeTradeRepository:
    def __init__(self, *, win_rate: float = 0.60) -> None:
        self.win_rate = win_rate

    async def get_trade_statistics(self):
        return {
            "total_trades": 10,
            "closed_trades": 10,
            "open_positions": 0,
            "winning_trades": int(self.win_rate * 10),
            "losing_trades": int((1 - self.win_rate) * 10),
            "win_rate": self.win_rate,
            "total_pnl": 10.0,
        }


class FakeSchedulerCycleRepository:
    pass


class FakeMarketClient:
    def __init__(self, market: GammaMarket) -> None:
        self.market = market

    async def fetch_market(self, market_id: str):
        if self.market.id == market_id:
            return self.market
        return None


def _build_signal():
    return SimpleNamespace(
        id=101,
        analysis_id=202,
        market_id="mkt-btc-1",
        market_question="Will BTC hit 150k?",
        market_price=0.61,
        execution_price=0.62,
        analysis=SimpleNamespace(direction=VerdictDirection.YES),
    )


def _build_market(*, yes_price: float = 0.62, no_price: float = 0.38) -> GammaMarket:
    return GammaMarket.model_validate(
        {
            "id": "mkt-btc-1",
            "question": "Will BTC hit 150k?",
            "outcomes": ["Yes", "No"],
            "outcomePrices": [str(yes_price), str(no_price)],
            "clobTokenIds": ["yes-token", "no-token"],
            "bestBid": 0.61,
            "bestAsk": 0.63,
            "active": True,
            "closed": False,
        }
    )


def _build_service(*, settings=None, live_repo=None, market=None, runtime_repo=None, trade_repo=None):
    signal = _build_signal()
    market = market or _build_market()
    return LiveExecutionService(
        settings=settings or build_test_settings(execution_mode="shadow"),
        signal_repository=FakeSignalRepository(signal),
        analysis_repository=FakeAnalysisRepository(),
        runtime_flag_repository=runtime_repo or FakeRuntimeFlagRepository(),
        execution_intent_repository=FakeExecutionIntentRepository(),
        live_trade_repository=live_repo or FakeLiveTradeRepository(),
        trade_repository=trade_repo or FakeTradeRepository(),
        scheduler_cycle_repository=FakeSchedulerCycleRepository(),
        market_client=FakeMarketClient(market),
        clob_client=StubCLOBClient(),
    )


@pytest.mark.asyncio
async def test_shadow_execution_creates_intent_and_audit_trail() -> None:
    service = _build_service(
        settings=build_test_settings(execution_mode="shadow"),
    )

    result = await service.simulate_execution(
        signal_id=101,
        approved_size_usd=5.0,
    )

    assert result.intent.intent_id == 1
    assert result.intent.status == "SIMULATED"
    assert result.intent.payload.asset_id == "yes-token"
    assert result.intent.payload.side == MarketSide.YES.value
    assert result.audit_trail


@pytest.mark.asyncio
async def test_live_execution_blocks_when_disabled() -> None:
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=False,
        )
    )

    with pytest.raises(LiveTradingDisabledError):
        await service.place_order(signal_id=101, approved_size_usd=3.0)


@pytest.mark.asyncio
async def test_live_execution_creates_order_and_position() -> None:
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=True,
            live_min_trade_size_usd=2.0,
            live_max_trade_size_usd=5.0,
            live_max_daily_exposure_usd=25.0,
            live_max_open_positions=1,
            live_require_phase_gate_passed=False,
        )
    )

    result = await service.place_order(signal_id=101, approved_size_usd=3.0)

    assert result.intent.intent_id == 1
    assert result.order_status == LiveOrderStatus.FILLED.value
    assert result.live_order_id == 1
    assert result.live_position_id == 1


@pytest.mark.asyncio
async def test_live_execution_blocks_when_paper_win_rate_below_live_minimum() -> None:
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=True,
            live_min_paper_win_rate=0.40,
        ),
        trade_repo=FakeTradeRepository(win_rate=0.20),
    )

    with pytest.raises(LiveEdgeGateBlockedError) as exc_info:
        await service.place_order(signal_id=101, approved_size_usd=3.0)

    assert "paper_win_rate_below_live_min:0.2000<0.4000" in str(exc_info.value)


@pytest.mark.asyncio
async def test_live_execution_blocks_when_phase_gate_is_hold(monkeypatch) -> None:
    class FakeProofOfEdgeService:
        def __init__(self, **kwargs) -> None:
            pass

        async def build_phase_gate_report(self, **kwargs):
            return SimpleNamespace(
                verdict="HOLD",
                reasons=["need_more_closed_trades:25<30"],
            )

    monkeypatch.setattr(live_execution_module, "ProofOfEdgeService", FakeProofOfEdgeService)
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=True,
            live_min_paper_win_rate=0.40,
            live_require_phase_gate_passed=True,
        ),
        trade_repo=FakeTradeRepository(win_rate=0.60),
    )

    with pytest.raises(LiveEdgeGateBlockedError) as exc_info:
        await service.place_order(signal_id=101, approved_size_usd=3.0)

    assert "paper_phase_gate_not_passed:HOLD" in str(exc_info.value)


@pytest.mark.asyncio
async def test_live_execution_triggers_circuit_breaker_at_daily_loss_limit() -> None:
    live_repo = FakeLiveTradeRepository()
    live_repo.positions.append(
        SimpleNamespace(
            id=1,
            market_id="mkt-btc-1",
            side=MarketSide.YES,
            entry_price=0.50,
            shares=100.0,
        )
    )
    runtime_repo = FakeRuntimeFlagRepository()
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=True,
            live_min_trade_size_usd=2.0,
            live_max_trade_size_usd=5.0,
            live_max_daily_exposure_usd=25.0,
            live_daily_loss_limit_usd=25.0,
            live_max_open_positions=3,
            live_require_phase_gate_passed=False,
        ),
        live_repo=live_repo,
        market=_build_market(yes_price=0.25, no_price=0.75),
        runtime_repo=runtime_repo,
    )

    with pytest.raises(CircuitBreakerTriggeredError) as exc_info:
        await service.place_order(signal_id=101, approved_size_usd=3.0)

    assert "live_daily_loss_limit_reached:-25.00<=-25.00" in str(exc_info.value)
    assert runtime_repo.flags["live_circuit_breaker"] is True


@pytest.mark.asyncio
async def test_reconcile_open_orders_detects_mismatch() -> None:
    live_repo = FakeLiveTradeRepository()
    live_repo.positions.append(
        SimpleNamespace(
            id=1,
            live_order=SimpleNamespace(exchange_order_id="exchange-1"),
        )
    )
    service = _build_service(
        settings=build_test_settings(
            execution_mode="live",
            live_trading_enabled=True,
            live_require_phase_gate_passed=False,
        ),
        live_repo=live_repo,
    )

    result = await service.reconcile_open_orders()

    assert result.status == "MISMATCH"
    assert result.mismatch_count == 1
    assert result.details["missing_on_exchange"] == ["exchange-1"]
